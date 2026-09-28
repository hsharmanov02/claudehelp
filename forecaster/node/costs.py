"""Charges every model call exactly once, refuses calls over the cap, and
writes each call's cost to the ledger as it happens.

The forecasting library's own accounting can't be relied on for money:

- Its only cap check runs inside a litellm callback, and litellm catches and
  logs the error, so calls go ahead after the cap is reached.
- A priced call is counted twice: once by the model wrapper right after the
  call, and again by a litellm callback that runs a moment later.
- A model newer than litellm's price list is priced at $0.

`CappedLlm` replaces that accounting for the node's own models. It checks the
cap before each attempt, lets the library make the call in isolation so none
of the library's counting reaches the node's budgets, then adds the call's cost
once (at a conservative fallback rate when the price is unknown) and reports it
to the spend recorder, which the adapter points at the ledger.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

from forecasting_tools import GeneralLlm
from forecasting_tools.ai_models.resource_managers.hard_limit_manager import (
    HardLimitExceededError,
    HardLimitManager,
)
from forecasting_tools.ai_models.resource_managers.monetary_cost_manager import (
    MonetaryCostManager,
)

logger = logging.getLogger(__name__)

SpendRecorder = Callable[[float, str], None]

_recorder: ContextVar[SpendRecorder | None] = ContextVar("node_spend_recorder", default=None)
_fallback_prices: ContextVar[tuple[float, float]] = ContextVar(
    "node_fallback_prices", default=(5.0, 25.0)
)


@contextmanager
def recording_spend(
    recorder: SpendRecorder, fallback_input_per_mtok: float, fallback_output_per_mtok: float
) -> Iterator[None]:
    """Within this block, every CappedLlm call is passed to `recorder(cost, model)`.

    Tasks started inside the block inherit it, so concurrent questions report too.
    """
    recorder_token = _recorder.set(recorder)
    prices_token = _fallback_prices.set((fallback_input_per_mtok, fallback_output_per_mtok))
    try:
        yield
    finally:
        _fallback_prices.reset(prices_token)
        _recorder.reset(recorder_token)


def refuse_if_over_budget() -> None:
    """Raises once any enclosing spending cap has been reached."""
    for manager in HardLimitManager.get_active_cost_managers():
        if manager.hard_limit and manager.current_usage >= manager.hard_limit:
            raise HardLimitExceededError(
                f"Spending cap reached: ${manager.current_usage:.4f} of ${manager.hard_limit:.2f}."
            )


def fallback_cost(prompt_tokens: int, completion_tokens: int) -> float:
    input_price, output_price = _fallback_prices.get()
    return ((prompt_tokens or 0) * input_price + (completion_tokens or 0) * output_price) / 1e6


class CappedLlm(GeneralLlm):
    """GeneralLlm whose calls stop at the cap and are counted once."""

    async def invoke(self, prompt, system_prompt: str | None = None) -> str:
        # Checked here too so a refused call fails at once instead of waiting
        # out the library's retry backoff.
        refuse_if_over_budget()
        return await super().invoke(prompt, system_prompt)

    async def _mockable_direct_call_to_model(self, prompt):
        refuse_if_over_budget()
        # Run the library's call with no budgets in scope. Both of its own
        # additions (the direct one and the late callback) then land on a
        # throwaway manager instead of the node's budgets.
        isolation = HardLimitManager._active_limit_managers.set([])
        try:
            response = await super()._mockable_direct_call_to_model(prompt)
        finally:
            HardLimitManager._active_limit_managers.reset(isolation)

        cost = float(response.cost or 0.0)
        if cost <= 0:
            cost = fallback_cost(response.prompt_tokens_used, response.completion_tokens_used)
            if cost > 0:
                logger.info(f"{self.model} has no known price; charged ${cost:.4f} at the fallback rate.")
        if cost > 0:
            MonetaryCostManager.increase_current_usage_in_parent_managers(cost)
            recorder = _recorder.get()
            if recorder is not None:
                recorder(cost, self.model)
        return response
