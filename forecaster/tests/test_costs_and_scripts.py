"""Money accounting through the real litellm path, the benchmark, and the ledger scripts."""

import asyncio
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from forecasting_tools import BinaryQuestion
from forecasting_tools.ai_models.resource_managers.hard_limit_manager import (
    HardLimitExceededError,
)
from forecasting_tools.ai_models.resource_managers.monetary_cost_manager import (
    MonetaryCostManager,
)

from node.costs import CappedLlm, recording_spend
from node.improve.benchmark import VARIANTS, run_benchmark
from node.models import ResolvedModels

REPO_ROOT = Path(__file__).resolve().parent.parent


async def test_each_call_is_counted_once_with_litellm_callbacks(monkeypatch):
    # A priced model and litellm's offline mock: litellm runs its real cost
    # callbacks, which made the library count every call twice.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    recorded = []
    with recording_spend(lambda cost, model: recorded.append(cost), 5, 25):
        with MonetaryCostManager(10) as run:
            llm = CappedLlm(model="openai/gpt-4o-mini", mock_response="Probability: 20%", allowed_tries=1)
            for _ in range(3):
                await llm.invoke("What happens next? " * 20)
            await asyncio.sleep(1.0)  # let litellm's late callbacks run
    assert len(recorded) == 3 and recorded[0] > 0
    assert run.current_usage == pytest.approx(sum(recorded))


async def test_refused_call_fails_without_retry_wait(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with MonetaryCostManager(1e-9):
        llm = CappedLlm(model="openai/gpt-4o-mini", mock_response="x", allowed_tries=2)
        await llm.invoke("first call is allowed")
        started = time.monotonic()
        with pytest.raises(HardLimitExceededError):
            await llm.invoke("second call is refused")
        assert time.monotonic() - started < 1


async def test_benchmark_scores_variants_and_records_spend(config, ledger, fake_models):
    models = ResolvedModels(forecasters=("a/one", "b/two"), helper="h/helper", search=None)
    fake = fake_models({"openrouter/a/one": "Probability: 70%", "openrouter/b/two": "Probability: 70%"})
    questions = [
        BinaryQuestion(
            question_text=f"Q{i}?", id_of_question=i, page_url=f"u{i}",
            community_prediction_at_access_time=0.8,
            close_time=datetime(2026, 12, 1, tzinfo=timezone.utc),
        )
        for i in range(3)
    ]
    scores = await run_benchmark(config, ledger, questions, list(VARIANTS), 20.0, 10.0, models=models)
    assert set(scores) == set(VARIANTS) and all(s is not None for s in scores.values())
    spend = [e for e in ledger.entries() if e["kind"] == "spend"]
    assert len(spend) == len(fake.calls) and all(e["buyer"] == "benchmark" for e in spend)
    assert [e["variant"] for e in ledger.entries() if e["kind"] == "benchmark"] == list(VARIANTS)


# ---------------------------------------------------------------- ledger scripts

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def checkout(tmp_path):
    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-q", "-b", "main", cwd=work)
    for name in ("load_ledger.sh", "save_ledger.sh"):
        target = work / "scripts" / name
        target.parent.mkdir(exist_ok=True)
        shutil.copy(REPO_ROOT / "scripts" / name, target)
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=work)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=work)
    git("remote", "add", "origin", str(remote), cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    return work


def run_script(name, cwd):
    return subprocess.run(["bash", f"scripts/{name}"], cwd=cwd, capture_output=True, text=True)


@needs_git
def test_ledger_round_trip_through_node_data_branch(checkout, tmp_path):
    assert run_script("load_ledger.sh", checkout).returncode == 0
    (checkout / "data" / "ledger" / "2026-09.jsonl").write_text('{"kind": "spend", "cost_usd": 1}\n')
    assert run_script("save_ledger.sh", checkout).returncode == 0
    fresh = tmp_path / "fresh"
    git("clone", "-q", "-b", "main", str(tmp_path / "remote.git"), str(fresh), cwd=tmp_path)
    assert run_script("load_ledger.sh", fresh).returncode == 0
    assert "cost_usd" in (fresh / "data" / "ledger" / "2026-09.jsonl").read_text()


@needs_git
def test_load_ledger_stops_when_github_is_unreachable(checkout):
    git("remote", "set-url", "origin", str(checkout.parent / "missing.git"), cwd=checkout)
    result = run_script("load_ledger.sh", checkout)
    assert result.returncode != 0
    assert not (checkout / "data").exists()
