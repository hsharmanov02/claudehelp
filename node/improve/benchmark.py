"""Compares node variants against the Metaculus community forecast.

Scoring against the community forecast on open questions gives a signal in
hours instead of waiting months for questions to resolve. This costs model
money, so it runs only when started by hand.

    python -m node.improve.benchmark --questions 60

A variant only earns a place in node.toml if it beats the current settings on
a large enough sample (the forecasting library's authors suggest 100+
questions to separate bots of similar skill).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
from pathlib import Path

import dotenv
from forecasting_tools import MetaculusApi
from forecasting_tools.cp_benchmarking.benchmarker import Benchmarker

from node.buyers.metaculus.adapter import build_forecaster
from node.config import NodeConfig, load_config
from node.costs import install_fallback_tracker
from node.guard import check_can_run
from node.ledger import Ledger
from node.models import resolve_models

logger = logging.getLogger(__name__)

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


async def run_benchmark(config: NodeConfig, ledger: Ledger, questions: int, names: list[str]) -> None:
    decision = check_can_run(config.guard, ledger)
    if not decision.allowed:
        print(f"Not running: {decision.reason}")
        return
    install_fallback_tracker(
        config.guard.fallback_input_price_per_mtok, config.guard.fallback_output_price_per_mtok
    )
    models = resolve_models(config.models)
    bots = []
    for name in names:
        bot = build_forecaster(variant_config(config, VARIANTS[name]), models, False, None)
        bot.skip_previously_forecasted_questions = False
        bots.append((name, bot))
    chosen = MetaculusApi.get_benchmark_questions(questions)
    benchmarker = Benchmarker(
        forecast_bots=[bot for _, bot in bots],
        questions_to_use=chosen,
        file_path_to_save_reports="data/benchmarks/",
    )
    from forecasting_tools.ai_models.resource_managers.monetary_cost_manager import (
        MonetaryCostManager,
    )

    with MonetaryCostManager(decision.run_budget_usd) as costs:
        results = await benchmarker.run_benchmark()
    ledger.append("spend", buyer="benchmark", cost_usd=round(costs.current_usage, 6))
    for (name, _), result in zip(bots, results):
        score = result.average_expected_baseline_score
        print(f"{name:18s} expected baseline score {score:7.2f}  cost ${result.total_cost or 0:.2f}")
        ledger.append(
            "benchmark",
            buyer="metaculus",
            variant=name,
            questions=len(chosen),
            expected_baseline_score=round(float(score), 3),
            cost_usd=round(float(result.total_cost or 0.0), 4),
        )


def main() -> None:
    dotenv.load_dotenv()
    parser = argparse.ArgumentParser(description="Benchmark node variants")
    parser.add_argument("--config", default="node.toml")
    parser.add_argument("--questions", type=int, default=60)
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = load_config(Path(args.config))
    asyncio.run(run_benchmark(config, Ledger(config.ledger_dir), args.questions, args.variants))


if __name__ == "__main__":
    main()
