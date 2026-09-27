"""Compares node variants against the Metaculus community forecast.

Scoring against the community forecast on open questions gives a signal in
hours instead of waiting months for questions to resolve. This costs model
money, so it runs only when started by hand.

    python -m node.improve.benchmark --questions 10 --budget-usd 20

Each variant gets an equal share of the budget and of the time, and all
variants are scored on the questions every one of them finished. A variant only
earns a place in node.toml if it beats the current settings on a large enough
sample (the forecasting library's authors suggest 100+ questions to separate
bots of similar skill), so raise both numbers for a real comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
from pathlib import Path

import dotenv
from forecasting_tools import BinaryQuestion, ForecastReport, MetaculusApi
from forecasting_tools.ai_models.resource_managers.monetary_cost_manager import (
    MonetaryCostManager,
)

from node.buyers.metaculus.adapter import build_forecaster
from node.config import NodeConfig, load_config
from node.costs import recording_spend
from node.guard import check_can_run
from node.ledger import Ledger
from node.models import ResolvedModels, resolve_models

logger = logging.getLogger(__name__)

BUYER = "benchmark"

# Each variant changes one thing relative to node.toml.
VARIANTS: dict[str, dict] = {
    "current": {},
    "median": {"binary_aggregation": "median"},
    "extremize_1.3": {"extremize": 1.3},
    "three_forecasts": {"forecasts_per_question": 3},
}


def variant_config(config: NodeConfig, changes: dict) -> NodeConfig:
    return dataclasses.replace(
        config, metaculus=dataclasses.replace(config.metaculus, **changes)
    )


async def run_variant(
    name: str,
    config: NodeConfig,
    models: ResolvedModels,
    questions: list[BinaryQuestion],
    budget_usd: float,
    minutes: float,
) -> dict[int, ForecastReport]:
    """Forecasts every question with one variant; returns the reports that finished."""
    finished: dict[int, ForecastReport] = {}

    def keep(question, result) -> None:
        if not isinstance(result, BaseException):
            finished[question.id_of_question] = result

    bot = build_forecaster(
        variant_config(config, VARIANTS[name]), models, False, None, on_question_done=keep
    )
    bot.skip_previously_forecasted_questions = False
    with MonetaryCostManager(budget_usd):
        try:
            async with asyncio.timeout(minutes * 60):
                await bot.forecast_questions(questions, return_exceptions=True)
        except TimeoutError:
            logger.warning(f"Variant {name} stopped at its {minutes:.0f}-minute share.")
    return finished


async def run_benchmark(
    config: NodeConfig,
    ledger: Ledger,
    questions: list[BinaryQuestion],
    names: list[str],
    budget_usd: float,
    minutes: float,
    models: ResolvedModels | None = None,
) -> dict[str, float | None]:
    decision = check_can_run(config.guard, ledger)
    if not decision.allowed:
        print(f"Not running: {decision.reason}")
        return {}
    spent_this_month = ledger.spend_for_month()
    budget_usd = min(budget_usd, config.guard.monthly_budget_usd - spent_this_month)
    models = models or resolve_models(config.models)
    guard = config.guard

    def record_spend(cost: float, model: str) -> None:
        ledger.append("spend", buyer=BUYER, cost_usd=round(cost, 6), model=model)

    results: dict[str, dict[int, ForecastReport]] = {}
    with recording_spend(
        record_spend, guard.fallback_input_price_per_mtok, guard.fallback_output_price_per_mtok
    ):
        for name in names:
            results[name] = await run_variant(
                name, config, models, questions, budget_usd / len(names), minutes / len(names)
            )

    common = set.intersection(*(set(r) for r in results.values())) if results else set()
    print(f"Scored on the {len(common)} questions every variant finished.")
    scores: dict[str, float | None] = {}
    for name, finished in results.items():
        reports = [finished[qid] for qid in sorted(common)]
        score = (
            ForecastReport.calculate_average_expected_baseline_score(reports) if reports else None
        )
        scores[name] = score
        cost = sum(float(r.price_estimate or 0.0) for r in finished.values())
        shown = f"{score:7.2f}" if score is not None else "    n/a"
        print(f"{name:18s} expected baseline score {shown}  "
              f"finished {len(finished)}/{len(questions)}  cost ${cost:.2f}")
        ledger.append(
            "benchmark",
            buyer="metaculus",
            variant=name,
            questions=len(common),
            expected_baseline_score=round(float(score), 3) if score is not None else None,
            cost_usd=round(cost, 4),
        )
    return scores


def main() -> None:
    dotenv.load_dotenv()
    parser = argparse.ArgumentParser(description="Benchmark node variants")
    parser.add_argument("--config", default="node.toml")
    parser.add_argument("--questions", type=int, default=10)
    parser.add_argument("--budget-usd", type=float, default=20.0,
                        help="Total model spend for all variants together")
    parser.add_argument("--minutes", type=float, default=100.0,
                        help="Total time for all variants together")
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = load_config(Path(args.config))
    ledger = Ledger(config.ledger_dir)
    # Fetched before the event loop starts; the library runs its own loop here.
    questions = MetaculusApi.get_benchmark_questions(args.questions)
    asyncio.run(
        run_benchmark(config, ledger, questions, args.variants, args.budget_usd, args.minutes)
    )


if __name__ == "__main__":
    main()
