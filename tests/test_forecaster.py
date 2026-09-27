"""End-to-end runs with simulated models and a simulated Metaculus."""

from datetime import datetime, timezone

from dataclasses import replace

import pytest
from forecasting_tools import (
    BinaryQuestion,
    BinaryReport,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericQuestion,
    Percentile,
)
from forecasting_tools.data_models.multiple_choice_report import MultipleChoiceReport
from forecasting_tools.data_models.numeric_report import NumericReport

from node.aggregate import geo_mean_odds
from node.buyers.metaculus.adapter import MetaculusBuyer, build_forecaster, tournament_ids
from node.buyers.metaculus.bot import OptionProbability
from node.cli import main
from node.models import ResolvedModels

MODELS = ResolvedModels(forecasters=("a/one", "b/two", "c/three"), helper="h/helper", search="s/search")
ROUTED = [MODELS.with_route(m) for m in MODELS.forecasters]


def binary(qid: int, forecasted: bool = False) -> BinaryQuestion:
    return BinaryQuestion(
        question_text=f"Will thing {qid} happen?",
        id_of_question=qid,
        id_of_post=qid,
        page_url=f"https://www.metaculus.com/questions/{qid}/",
        resolution_criteria="Resolves Yes if it happens.",
        background_info="Background.",
        fine_print="",
        close_time=datetime(2026, 10, 1, tzinfo=timezone.utc),
        already_forecasted=forecasted,
    )


def multiple_choice(qid: int) -> MultipleChoiceQuestion:
    return MultipleChoiceQuestion(
        question_text="Which colour?",
        id_of_question=qid,
        page_url=f"https://www.metaculus.com/questions/{qid}/",
        options=["Red", "Blue", "Green"],
        resolution_criteria="",
        background_info="",
        fine_print="",
    )


def numeric(qid: int) -> NumericQuestion:
    return NumericQuestion(
        question_text="How many?",
        id_of_question=qid,
        page_url=f"https://www.metaculus.com/questions/{qid}/",
        upper_bound=100.0,
        lower_bound=0.0,
        open_upper_bound=True,
        open_lower_bound=False,
        unit_of_measure="widgets",
        resolution_criteria="",
        background_info="",
        fine_print="",
    )


class FakeClient:
    CURRENT_AI_COMPETITION_ID = 1001
    CURRENT_MINIBENCH_ID = "minibench"

    def __init__(self, by_tournament):
        self.by_tournament = by_tournament
        self.requested = []

    def get_all_open_questions_from_tournament(self, tournament):
        self.requested.append(tournament)
        return list(self.by_tournament.get(tournament, []))


@pytest.fixture(autouse=True)
def never_publish(monkeypatch):
    calls = []

    async def fake_publish(self, metaculus_client=None):
        calls.append(self.question.page_url)

    for report_type in (BinaryReport, MultipleChoiceReport, NumericReport):
        monkeypatch.setattr(report_type, "publish_report_to_metaculus", fake_publish)
    return calls


def buyer_for(config, ledger, client):
    return MetaculusBuyer(config, ledger, client=client, model_resolver=lambda: MODELS)


async def test_binary_ensemble_rotates_models_and_combines(config, ledger, fake_models, never_publish):
    fake = fake_models(
        {
            ROUTED[0]: "Probability: 20%",
            ROUTED[1]: "Probability: 40%",
            ROUTED[2]: "Probability: 60%",
        }
    )
    client = FakeClient({1001: [binary(1)], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)

    assert summary.forecasts_made == 1 and not summary.errors
    forecaster_calls = [m for m, _ in fake.calls if m in ROUTED]
    assert len(forecaster_calls) == config.metaculus.forecasts_per_question
    assert {m: forecaster_calls.count(m) for m in ROUTED} == {m: 2 for m in ROUTED}
    forecast = next(e for e in ledger.entries() if e["kind"] == "forecast")
    expected = geo_mean_odds([0.2, 0.2, 0.4, 0.4, 0.6, 0.6])
    assert forecast["prediction"] == f"{round(expected * 100, 2)}%"
    assert forecast["published"] is False
    assert never_publish == []
    kinds = [e["kind"] for e in ledger.entries()]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"


async def test_publish_flag_reaches_metaculus(config, ledger, fake_models, never_publish):
    fake_models({m: "Probability: 50%" for m in ROUTED})
    client = FakeClient({1001: [binary(7)], "minibench": []})
    await buyer_for(config, ledger, client).run("tournament", publish=True, run_budget_usd=5)
    assert never_publish == ["https://www.metaculus.com/questions/7/"]


async def test_skips_already_forecasted_unsupported_and_duplicates(config, ledger, fake_models):
    fake_models({m: "Probability: 50%" for m in ROUTED})
    plain = MetaculusQuestion(question_text="Group question", id_of_question=99, page_url="u99")
    client = FakeClient({1001: [binary(1, forecasted=True), binary(2), plain], "minibench": [binary(2)]})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    assert summary.forecasts_made == 1
    assert summary.questions_skipped == 2  # one already forecast, one unsupported type
    assert client.requested == [1001, "minibench"]


async def test_test_mode_uses_bot_testing_area_and_reforecasts(config, ledger, fake_models):
    fake_models({m: "Probability: 50%" for m in ROUTED})
    client = FakeClient({"bot-testing-area": [binary(3, forecasted=True)]})
    summary = await buyer_for(config, ledger, client).run("test", publish=False, run_budget_usd=5)
    assert client.requested == ["bot-testing-area"]
    assert summary.forecasts_made == 1


async def test_multiple_choice_and_numeric(config, ledger, fake_models):
    mc_answer = "Thinking...\nRed: 50%\nBlue: 30%\nGreen: 20%"
    num_answer = "\n".join(
        f"Percentile {p}: {v}" for p, v in [(10, 5), (20, 10), (40, 20), (60, 30), (80, 45), (90, 60)]
    )
    fake_models({m: [mc_answer, num_answer] * 10 for m in ROUTED})
    # Answers are consumed in call order, so give each question its own run.
    client = FakeClient({1001: [multiple_choice(10)], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    assert summary.forecasts_made == 1, summary.errors

    fake_models({m: num_answer for m in ROUTED})
    client = FakeClient({1001: [numeric(11)], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    assert summary.forecasts_made == 1, summary.errors
    forecasts = [e for e in ledger.entries() if e["kind"] == "forecast"]
    assert "Red" in forecasts[0]["prediction"]
    assert forecasts[1]["question_type"] == "NumericQuestion"


async def test_unparseable_answers_become_errors_not_crashes(config, ledger, fake_models, monkeypatch):
    fake_models({m: "I refuse to give a number." for m in ROUTED})

    async def failing_parser(*args, **kwargs):
        raise ValueError("parser could not read it")

    monkeypatch.setattr("node.buyers.metaculus.bot.structure_output", failing_parser)
    client = FakeClient({1001: [binary(5)], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    assert summary.forecasts_made == 0 and len(summary.errors) == 1
    assert summary.all_failed
    assert any(e["kind"] == "forecast_error" for e in ledger.entries())


async def test_run_budget_stops_spending_and_is_recorded(config, ledger, fake_models):
    fake = fake_models({m: "Probability: 50%" for m in ROUTED})
    fake.cost_per_call = 0.40
    questions = [binary(i) for i in range(20, 30)]
    client = FakeClient({1001: questions, "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=1.0)
    # Unchecked, this run makes 80 calls ($32). Once the cap is reached,
    # further calls are refused; only calls already in flight can finish.
    in_flight = 2 * (config.metaculus.forecasts_per_question + 1)
    assert len(fake.calls) <= 1.0 / 0.40 + in_flight
    assert summary.cost_usd <= 1.0 + 0.40 * in_flight
    assert summary.errors and summary.forecasts_made < len(questions)
    spend = [e for e in ledger.entries() if e["kind"] == "spend"]
    assert len(spend) == len(fake.calls)
    assert sum(e["cost_usd"] for e in spend) == pytest.approx(summary.cost_usd, rel=1e-6)


async def test_unpriced_model_is_charged_at_fallback_rate(config, ledger, fake_models):
    fake = fake_models({m: "Probability: 50%" for m in ROUTED})
    fake.cost_per_call = 0.0
    client = FakeClient({1001: [binary(1)], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    per_call = (
        fake.prompt_tokens * config.guard.fallback_input_price_per_mtok
        + fake.completion_tokens * config.guard.fallback_output_price_per_mtok
    ) / 1e6
    assert summary.cost_usd == pytest.approx(per_call * len(fake.calls))
    assert ledger.spend_for_month() == pytest.approx(summary.cost_usd)


async def test_dry_runs_forecast_each_question_once(config, ledger, fake_models):
    fake = fake_models({m: "Probability: 50%" for m in ROUTED})
    client = FakeClient({1001: [binary(1), binary(2)], "minibench": []})
    buyer = buyer_for(config, ledger, client)
    first = await buyer.run("tournament", publish=False, run_budget_usd=5)
    calls_after_first = len(fake.calls)
    second = await buyer.run("tournament", publish=False, run_budget_usd=5)
    assert first.forecasts_made == 2
    assert second.forecasts_made == 0 and second.questions_skipped == 2
    assert len(fake.calls) == calls_after_first and second.cost_usd == 0
    # Going live forecasts them for real once, then skips them.
    live = await buyer.run("tournament", publish=True, run_budget_usd=5)
    again = await buyer.run("tournament", publish=True, run_budget_usd=5)
    assert live.forecasts_made == 2 and again.forecasts_made == 0


async def test_forecast_without_ledger_record_is_posted_again(config, ledger, fake_models, never_publish):
    # Metaculus shows a forecast, but the ledger has none: for example the
    # forecast went up and its required reasoning comment didn't.
    fake_models({m: "Probability: 50%" for m in ROUTED})
    client = FakeClient({1001: [binary(8, forecasted=True)], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=True, run_budget_usd=5)
    assert summary.forecasts_made == 1
    assert never_publish == ["https://www.metaculus.com/questions/8/"]


async def test_time_limit_keeps_finished_results(config, ledger, fake_models):
    fake = fake_models({m: "Probability: 50%" for m in ROUTED})
    fake.delay = lambda model, prompt: 30 if "thing 2" in prompt else 0
    config = replace(config, guard=replace(config.guard, max_run_minutes=0.02))
    client = FakeClient({1001: [binary(1), binary(2)], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    assert summary.forecasts_made == 1
    assert any("minute limit" in e and "1 questions left" in e for e in summary.errors)
    kinds = [e["kind"] for e in ledger.entries()]
    assert "forecast" in kinds and kinds[-1] == "run_finished"
    assert ledger.spend_for_month() == pytest.approx(summary.cost_usd)


async def test_multiple_choice_with_many_zero_options(config, ledger, fake_models):
    options = [f"Party {i}" for i in range(14)]
    answer = "\n".join(
        f"{o}: {p}%" for o, p in zip(options, [60, 25, 15] + [0] * 11)
    )
    fake_models({m: answer for m in ROUTED})
    question = MultipleChoiceQuestion(
        question_text="Who wins?", id_of_question=40, page_url="u40", options=options,
    )
    client = FakeClient({1001: [question], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    assert summary.forecasts_made == 1, summary.errors


async def test_multiple_choice_model_parser_fallback_floors_zeros(config, ledger, fake_models, monkeypatch):
    options = [f"Party {i}" for i in range(14)]
    fake_models({m: "I'd say Party 0 is most likely." for m in ROUTED})
    calls = []

    async def parser(text_to_structure, output_type, model, additional_instructions=None, **kwargs):
        calls.append(output_type)
        values = [0.6, 0.25, 0.15] + [0.0] * 11
        return [OptionProbability(option_name=o, probability=p) for o, p in zip(options, values)]

    monkeypatch.setattr("node.buyers.metaculus.bot.structure_output", parser)
    question = MultipleChoiceQuestion(
        question_text="Who wins?", id_of_question=41, page_url="u41", options=options,
    )
    client = FakeClient({1001: [question], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    assert summary.forecasts_made == 1, summary.errors
    assert len(calls) == config.metaculus.forecasts_per_question


async def test_numeric_answer_in_wrong_units_falls_back_to_model_parser(config, ledger, fake_models, monkeypatch):
    question = NumericQuestion(
        question_text="Revenue in billions?", id_of_question=50, page_url="u50",
        upper_bound=10.0, lower_bound=0.0, open_upper_bound=False, open_lower_bound=False,
        unit_of_measure="B $",
    )
    in_millions = "\n".join(
        f"Percentile {p}: {v}" for p, v in zip((10, 20, 40, 60, 80, 90), (800, 900, 1000, 1100, 1150, 1200))
    )
    fake_models({m: in_millions for m in ROUTED})
    calls = []

    async def parser(text_to_structure, output_type, model, additional_instructions=None, **kwargs):
        calls.append(additional_instructions)
        return [
            Percentile(percentile=p / 100, value=v)
            for p, v in zip((10, 20, 40, 60, 80, 90), (0.8, 0.9, 1.0, 1.1, 1.15, 1.2))
        ]

    monkeypatch.setattr("node.buyers.metaculus.bot.structure_output", parser)
    client = FakeClient({1001: [question], "minibench": []})
    summary = await buyer_for(config, ledger, client).run("tournament", publish=False, run_budget_usd=5)
    assert summary.forecasts_made == 1, summary.errors
    assert len(calls) == config.metaculus.forecasts_per_question


def test_cli_kill_switch_blocks_without_touching_network(config, monkeypatch, capsys, tmp_path):
    (tmp_path / "node.toml").write_text(
        (__import__("pathlib").Path(__file__).parent.parent / "node.toml").read_text()
    )
    monkeypatch.setenv("NODE_KILL", "1")
    assert main(["run", "metaculus"]) == 0
    assert "Kill switch" in capsys.readouterr().out
    lines = list((tmp_path / "data" / "ledger").glob("*.jsonl"))
    assert lines and "run_blocked" in lines[0].read_text()


def test_cli_status(config, monkeypatch, capsys, tmp_path):
    (tmp_path / "node.toml").write_text(
        (__import__("pathlib").Path(__file__).parent.parent / "node.toml").read_text()
    )
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Publishing:" in out and "dry runs only" in out


def test_build_forecaster_uses_openrouter_routes(config):
    bot = build_forecaster(config, MODELS, publish=False, client=FakeClient({}))
    assert [f.model for f in bot.forecasters] == ROUTED
    assert bot.get_llm("parser", "llm").model == "openrouter/h/helper"
    assert bot.predictions_per_research_report == config.metaculus.forecasts_per_question
    assert bot.get_config()["settings"]


def test_seasonal_override():
    client = FakeClient({})
    assert tournament_ids(("seasonal", "minibench"), client) == [1001, "minibench"]
    assert tournament_ids(("seasonal",), client, "spring-futureeval-2027") == ["spring-futureeval-2027"]
