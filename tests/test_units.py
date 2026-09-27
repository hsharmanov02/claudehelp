import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from node.aggregate import combine_binary, combine_multiple_choice, geo_mean_odds
from node.config import ConfigError, parse_config
from node.guard import check_can_run, publishing_enabled
from node.ledger import Ledger
from node.models import ResolvedModels, pick, resolve_models
from node.config import ModelSettings
from node.parsing import (
    parse_binary,
    parse_date_percentiles,
    parse_multiple_choice,
    parse_percentiles,
)

MINIMAL = {
    "models": {"forecasters": [["a/x"]], "helper": ["a/h"]},
}


# ---------------------------------------------------------------- config

def test_repo_config_loads(config):
    assert config.metaculus.targets == ("seasonal", "minibench")
    assert len(config.models.forecasters) == 3
    assert config.guard.monthly_budget_usd > 0


@pytest.mark.parametrize("target", ["metaculus_cup", "cup"])
def test_human_tournaments_are_refused(target):
    with pytest.raises(ConfigError, match="bots may not enter"):
        parse_config({**MINIMAL, "metaculus": {"targets": [target]}})


def test_unknown_target_refused():
    with pytest.raises(ConfigError, match="Unknown target"):
        parse_config({**MINIMAL, "metaculus": {"targets": ["somewhere"]}})


@pytest.mark.parametrize(
    "bad",
    [
        {"guard": {"monthly_budget_usd": 0}},
        {"metaculus": {"forecasts_per_question": 0}},
        {"metaculus": {"extremize": 5}},
        {"metaculus": {"binary_aggregation": "mean"}},
        {"models": {"forecasters": [], "helper": ["a/h"]}},
        {"models": {"forecasters": [["a/x"]], "helper": []}},
    ],
)
def test_bad_config_rejected(bad):
    with pytest.raises(ConfigError):
        parse_config({**MINIMAL, **bad})


# ---------------------------------------------------------------- aggregation

def test_geo_mean_odds_of_equal_values_is_that_value():
    assert geo_mean_odds([0.3, 0.3, 0.3]) == pytest.approx(0.3)


def test_geo_mean_odds_matches_formula():
    ps = [0.2, 0.5, 0.8, 0.9]
    odds = [p / (1 - p) for p in ps]
    expected_odds = math.prod(odds) ** (1 / len(odds))
    assert geo_mean_odds(ps) == pytest.approx(expected_odds / (1 + expected_odds))


def test_extremize_pushes_away_from_half():
    assert geo_mean_odds([0.7, 0.7], extremize=1.5) > 0.7
    assert geo_mean_odds([0.3, 0.3], extremize=1.5) < 0.3
    assert geo_mean_odds([0.5, 0.5], extremize=2.0) == pytest.approx(0.5)


def test_extremes_are_clamped():
    assert geo_mean_odds([0.0, 0.0]) == pytest.approx(0.01)
    assert geo_mean_odds([1.0]) == pytest.approx(0.99)
    assert combine_binary([0.0, 1.0, 1.0], "median") == pytest.approx(0.99)


def test_multiple_choice_average_floor_and_normalize():
    combined = combine_multiple_choice(
        [{"A": 0.6, "B": 0.4, "C": 0.0}, {"A": 0.8, "B": 0.2, "C": 0.0}],
        ["A", "B", "C"],
        floor=0.01,
    )
    assert sum(combined.values()) == pytest.approx(1.0)
    assert combined["C"] > 0
    assert combined["A"] > combined["B"] > combined["C"]


def test_multiple_choice_missing_option_rejected():
    with pytest.raises(ValueError, match="missing"):
        combine_multiple_choice([{"A": 1.0}], ["A", "B"])


# ---------------------------------------------------------------- parsing

def test_parse_binary_takes_last_answer():
    text = "Early guess Probability: 20%\n... revised.\nProbability: 35.5%"
    assert parse_binary(text) == pytest.approx(0.355)


@pytest.mark.parametrize("text", ["no number here", "Probability: 150%"])
def test_parse_binary_rejects(text):
    assert parse_binary(text) is None


def test_parse_binary_handles_bold():
    assert parse_binary("**Probability: 7%**") == pytest.approx(0.07)


def test_parse_multiple_choice_percentages():
    text = "reasoning...\nOption_A: nope\nRed: 50%\nBlue: 30%\nGreen: 20%"
    assert parse_multiple_choice(text, ["Red", "Blue", "Green"]) == pytest.approx(
        {"Red": 0.5, "Blue": 0.3, "Green": 0.2}
    )


def test_parse_multiple_choice_prefix_names_not_confused():
    text = "0-10: 30%\n0-100: 70%"
    assert parse_multiple_choice(text, ["0-10", "0-100"]) == pytest.approx(
        {"0-10": 0.3, "0-100": 0.7}
    )


def test_parse_multiple_choice_decimals_and_missing():
    assert parse_multiple_choice("A: 0.25\nB: 0.75", ["A", "B"]) == pytest.approx(
        {"A": 0.25, "B": 0.75}
    )
    assert parse_multiple_choice("A: 25%", ["A", "B"]) is None
    assert parse_multiple_choice("A: 10%\nB: 10%", ["A", "B"]) is None


def test_parse_percentiles_with_commas():
    text = "\n".join(
        f"Percentile {p}: {v}"
        for p, v in [(10, "1,000"), (20, "2,500"), (40, "4,000"), (60, "5,000"), (80, "7,500"), (90, "10,000.5")]
    )
    assert parse_percentiles(text) == [
        (0.1, 1000.0), (0.2, 2500.0), (0.4, 4000.0), (0.6, 5000.0), (0.8, 7500.0), (0.9, 10000.5)
    ]


@pytest.mark.parametrize(
    "values",
    [
        ["1", "2", "3", "3", "5", "6"],  # not strictly increasing
        ["1e3", "2e3", "3e3", "4e3", "5e3", "6e3"],  # scientific notation
    ],
)
def test_parse_percentiles_rejects(values):
    text = "\n".join(f"Percentile {p}: {v}" for p, v in zip((10, 20, 40, 60, 80, 90), values))
    assert parse_percentiles(text) is None


def test_parse_negative_percentiles():
    values = ["-5", "-2.5", "-1", "0", "1.5", "3"]
    text = "\n".join(f"Percentile {p}: {v}" for p, v in zip((10, 20, 40, 60, 80, 90), values))
    assert [v for _, v in parse_percentiles(text)] == [-5, -2.5, -1, 0, 1.5, 3]


def test_parse_date_percentiles():
    dates = ["2026-10-01", "2026-10-15", "2026-11-01", "2026-11-20", "2026-12-10", "2027-01-05T12:00:00Z"]
    text = "\n".join(f"Percentile {p}: {d}" for p, d in zip((10, 20, 40, 60, 80, 90), dates))
    parsed = parse_date_percentiles(text)
    assert parsed[0] == (0.1, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert parsed[-1][1] == datetime(2027, 1, 5, 12, tzinfo=timezone.utc)


# ---------------------------------------------------------------- ledger and guard

def test_ledger_round_trip_and_month_spend(tmp_path):
    ledger = Ledger(tmp_path)
    sept = datetime(2026, 9, 27, tzinfo=timezone.utc)
    oct_ = datetime(2026, 10, 2, tzinfo=timezone.utc)
    ledger.append("spend", when=sept, cost_usd=1.25)
    ledger.append("spend", when=sept, cost_usd=0.75)
    ledger.append("forecast", when=sept, cost_usd=99)  # not a spend entry
    ledger.append("spend", when=oct_, cost_usd=5)
    assert ledger.spend_for_month(sept) == pytest.approx(2.0)
    assert ledger.spend_for_month(oct_) == pytest.approx(5.0)
    assert len(list(ledger.entries())) == 4


def test_ledger_skips_torn_lines(tmp_path):
    ledger = Ledger(tmp_path)
    when = datetime(2026, 9, 27, tzinfo=timezone.utc)
    ledger.append("spend", when=when, cost_usd=1)
    with (tmp_path / "2026-09.jsonl").open("a") as f:
        f.write('{"kind": "spend", "cost_')
    assert ledger.spend_for_month(when) == pytest.approx(1)


def test_guard_allows_with_run_budget(config, ledger):
    decision = check_can_run(config.guard, ledger)
    assert decision.allowed
    assert decision.run_budget_usd == pytest.approx(config.guard.max_run_cost_usd)


def test_guard_run_budget_limited_by_month(config, ledger):
    ledger.append("spend", cost_usd=config.guard.monthly_budget_usd - 2)
    decision = check_can_run(config.guard, ledger)
    assert decision.allowed and decision.run_budget_usd == pytest.approx(2)


def test_guard_blocks_when_budget_used(config, ledger):
    ledger.append("spend", cost_usd=config.guard.monthly_budget_usd)
    decision = check_can_run(config.guard, ledger)
    assert not decision.allowed and "budget" in decision.reason


def test_kill_switch_env_and_file(config, ledger, monkeypatch, tmp_path):
    monkeypatch.setenv("NODE_KILL", "true")
    assert not check_can_run(config.guard, ledger).allowed
    monkeypatch.delenv("NODE_KILL")
    kill = tmp_path / "KILL"
    kill.write_text("stop")
    assert not check_can_run(config.guard, ledger, kill_file=kill).allowed


def test_publish_needs_both_switches(monkeypatch):
    assert not publishing_enabled(True)
    monkeypatch.setenv("NODE_PUBLISH", "true")
    assert publishing_enabled(True)
    assert not publishing_enabled(False)


# ---------------------------------------------------------------- models

def test_pick_prefers_first_available():
    assert pick(["x/new", "x/old"], {"x/old"}) == "x/old"
    assert pick(["x/new", "x/old"], {"x/new", "x/old"}) == "x/new"
    assert pick(["x/new", "x/old"], None) == "x/new"
    assert pick(["x/new"], set()) == "x/new"


def test_resolve_models_and_route():
    settings = ModelSettings(forecasters=(("a/1", "a/2"), ("b/1",)), helper=("h/1",), search=())
    resolved = resolve_models(settings, catalog_fetcher=lambda: {"a/2", "b/1", "h/1"})
    assert resolved.forecasters == ("a/2", "b/1")
    assert resolved.search is None
    assert resolved.with_route("a/2") == "openrouter/a/2"
    assert resolved.with_route("openrouter/a/2") == "openrouter/a/2"
