"""Makes sure every model call counts toward the spending cap.

The forecasting library prices calls with litellm's price list. A model newer
than that list is priced at $0, which would let the node overspend without
noticing. This tracker charges such calls at a conservative fallback rate.
"""

from __future__ import annotations

import logging

import litellm
from forecasting_tools.ai_models.resource_managers.monetary_cost_manager import (
    LitellmCostTracker,
    MonetaryCostManager,
)
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger(__name__)


class FallbackCostTracker(CustomLogger):
    def __init__(self, input_price_per_mtok: float, output_price_per_mtok: float):
        super().__init__()
        self.input_price_per_mtok = input_price_per_mtok
        self.output_price_per_mtok = output_price_per_mtok

    def log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: D401
        self._charge_if_unpriced(kwargs, response_obj)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._charge_if_unpriced(kwargs, response_obj)

    def fallback_cost(self, response_obj) -> float:
        usage = getattr(response_obj, "usage", None)
        if usage is None:
            return 0.0
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        return (
            prompt_tokens * self.input_price_per_mtok
            + completion_tokens * self.output_price_per_mtok
        ) / 1_000_000

    def _charge_if_unpriced(self, kwargs, response_obj) -> None:
        # Same two sources the library's own tracker reads; it charges the max
        # of them, so we only step in when both are zero.
        known = max(
            LitellmCostTracker.extract_cost_from_hidden_params(kwargs) or 0.0,
            LitellmCostTracker.extract_cost_from_response_obj(response_obj) or 0.0,
        )
        if known > 0:
            return
        cost = self.fallback_cost(response_obj)
        if cost > 0:
            logger.info(
                f"Model {kwargs.get('model')} has no known price; charging "
                f"${cost:.4f} at the fallback rate."
            )
            MonetaryCostManager.increase_current_usage_in_parent_managers(cost)


def install_fallback_tracker(
    input_price_per_mtok: float, output_price_per_mtok: float
) -> FallbackCostTracker:
    """Registers the tracker once per process and returns it."""
    for handler in litellm.callbacks:
        if isinstance(handler, FallbackCostTracker):
            handler.input_price_per_mtok = input_price_per_mtok
            handler.output_price_per_mtok = output_price_per_mtok
            return handler
    tracker = FallbackCostTracker(input_price_per_mtok, output_price_per_mtok)
    litellm.callbacks.append(tracker)
    return tracker
