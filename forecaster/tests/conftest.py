import asyncio
import os
from pathlib import Path

import pytest

os.environ.setdefault("METACULUS_TOKEN", "test-token")

from forecasting_tools import GeneralLlm  # noqa: E402
from forecasting_tools.ai_models.model_interfaces.ai_model import AiModel  # noqa: E402
from litellm import ModelResponse  # noqa: E402
from litellm.types.utils import Choices, Message, Usage  # noqa: E402

from node.config import load_config  # noqa: E402
from node.ledger import Ledger  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config(tmp_path):
    base = load_config(REPO_ROOT / "node.toml")
    from dataclasses import replace

    return replace(base, ledger_dir=tmp_path / "ledger")


@pytest.fixture
def ledger(config):
    return Ledger(config.ledger_dir)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in ("NODE_KILL", "NODE_PUBLISH", "ASKNEWS_CLIENT_ID", "ASKNEWS_SECRET", "ASKNEWS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


class FakeModels:
    """Scripted answers keyed by model name. Records every call.

    Replaces only the network request inside the forecasting library, so each
    call still goes through the node's CappedLlm and the library's own call
    handling, including the spending cap and cost accounting.
    """

    def __init__(self, answers: dict[str, list[str] | str], default: str = "summary"):
        self.answers = {k: (v if isinstance(v, list) else [v]) for k, v in answers.items()}
        self.default = default
        self.calls: list[tuple[str, str]] = []
        # Price the provider reports per call. 0 means "unknown price", which
        # the node charges at the fallback rate from the token counts below.
        self.cost_per_call = 0.01
        self.prompt_tokens = 1000
        self.completion_tokens = 500
        # Seconds to wait before answering, by (model, prompt).
        self.delay = lambda model, prompt: 0

    def respond(self, llm: GeneralLlm, prompt) -> ModelResponse:
        self.calls.append((llm.model, str(prompt)))
        queue = self.answers.get(llm.model)
        if not queue:
            text = self.default
        else:
            text = queue.pop(0) if len(queue) > 1 else queue[0]
        response = ModelResponse(
            model=llm.model,
            choices=[
                Choices(
                    index=0,
                    finish_reason="stop",
                    message=Message(role="assistant", content=text),
                )
            ],
            usage=Usage(
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                total_tokens=self.prompt_tokens + self.completion_tokens,
            ),
        )
        response._hidden_params["response_cost"] = self.cost_per_call
        return response


@pytest.fixture
def fake_models(monkeypatch):
    holder = {}
    # The library caps model calls per test as a guard against real API use;
    # these calls never leave the process.
    monkeypatch.setattr(
        AiModel, "_increment_calls_then_error_if_testing_call_limit_reached", lambda self, *a: None
    )

    def install(answers, default="summary"):
        fake = FakeModels(answers, default)

        async def call(self, prompt):
            await asyncio.sleep(fake.delay(self.model, str(prompt)))
            return fake.respond(self, prompt)

        monkeypatch.setattr(GeneralLlm, "_call_litellm_dropping_deprecated_temperature", call)
        holder["fake"] = fake
        return fake

    return install
