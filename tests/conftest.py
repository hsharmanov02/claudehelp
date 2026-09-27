import os
from pathlib import Path

import pytest

os.environ.setdefault("METACULUS_TOKEN", "test-token")

from forecasting_tools import GeneralLlm  # noqa: E402

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
    """Scripted answers keyed by model name. Records every call."""

    def __init__(self, answers: dict[str, list[str] | str], default: str = "summary"):
        self.answers = {k: (v if isinstance(v, list) else [v]) for k, v in answers.items()}
        self.default = default
        self.calls: list[tuple[str, str]] = []
        self.cost_per_call = 0.0

    async def invoke(self, llm: GeneralLlm, prompt) -> str:
        from forecasting_tools.ai_models.resource_managers.monetary_cost_manager import (
            MonetaryCostManager,
        )

        MonetaryCostManager.raise_error_if_limit_would_be_reached()
        self.calls.append((llm.model, str(prompt)))
        if self.cost_per_call:
            MonetaryCostManager.increase_current_usage_in_parent_managers(self.cost_per_call)
        queue = self.answers.get(llm.model)
        if not queue:
            return self.default
        return queue.pop(0) if len(queue) > 1 else queue[0]


@pytest.fixture
def fake_models(monkeypatch):
    holder = {}

    def install(answers, default="summary"):
        fake = FakeModels(answers, default)

        async def invoke(self, prompt):
            return await fake.invoke(self, prompt)

        monkeypatch.setattr(GeneralLlm, "invoke", invoke)
        holder["fake"] = fake
        return fake

    return install
