"""Command line: `python -m node <command>`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import dotenv

from node.config import ConfigError, load_config
from node.guard import check_can_run, env_flag, kill_switch_on, publishing_enabled
from node.ledger import Ledger
from node.scoreboard import format_summary, month_summary

logger = logging.getLogger("node")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.setLevel(logging.INFO)


def cmd_run(args, config, ledger) -> int:
    decision = check_can_run(config.guard, ledger)
    if not decision.allowed:
        ledger.append("run_blocked", buyer=args.buyer, reason=decision.reason)
        print(f"Not running: {decision.reason}")
        return 0
    publish = publishing_enabled(args.publish)
    if args.publish and not publish:
        print("Dry run: publishing needs NODE_PUBLISH=true as well as --publish.")
    if not os.getenv("METACULUS_TOKEN"):
        print("METACULUS_TOKEN is not set.")
        return 2
    if not os.getenv("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set.")
        return 2

    from node.buyers.metaculus.adapter import MetaculusBuyer

    buyer = MetaculusBuyer(config, ledger)
    summary = asyncio.run(buyer.run(args.mode, publish, decision.run_budget_usd))
    print(
        f"{'Published' if publish else 'Dry run'}: {summary.forecasts_made} forecasts, "
        f"{len(summary.errors)} errors, {summary.questions_skipped} skipped, "
        f"${summary.cost_usd:.2f} spent."
    )
    for error in summary.errors[:10]:
        print(f"  error: {error}")
    spent = ledger.spend_for_month()
    print(f"This month: ${spent:.2f} of ${config.guard.monthly_budget_usd:.2f} spent.")
    return 1 if summary.all_failed else 0


def cmd_status(args, config, ledger) -> int:
    decision = check_can_run(config.guard, ledger)
    print(f"Kill switch:          {'ON' if kill_switch_on() else 'off'}")
    print(f"Publishing:           {'on' if env_flag('NODE_PUBLISH') else 'off (dry runs only)'}")
    print(f"Can run now:          {'yes' if decision.allowed else 'no'} ({decision.reason})")
    print(format_summary(month_summary(ledger), config.guard.monthly_budget_usd))
    return 0


def cmd_scoreboard(args, config, ledger) -> int:
    print(json.dumps(month_summary(ledger), indent=2, default=str))
    return 0


def cmd_models(args, config, ledger) -> int:
    from node.models import resolve_models

    models = resolve_models(config.models)
    print(json.dumps(
        {"forecasters": models.forecasters, "helper": models.helper, "search": models.search},
        indent=2,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="node", description="Protocol Buyer Node")
    parser.add_argument("--config", default="node.toml", help="Path to node.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Do one round of work for a buyer")
    run.add_argument("buyer", choices=["metaculus"])
    run.add_argument("--mode", choices=["tournament", "test"], default="tournament")
    run.add_argument(
        "--publish",
        action="store_true",
        help="Post forecasts (also needs NODE_PUBLISH=true in the environment)",
    )
    run.set_defaults(func=cmd_run)

    sub.add_parser("status", help="Kill switch, budget and last run").set_defaults(func=cmd_status)
    sub.add_parser("scoreboard", help="This month's ledger summary as JSON").set_defaults(
        func=cmd_scoreboard
    )
    sub.add_parser("models", help="Show which models the node would use").set_defaults(
        func=cmd_models
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    dotenv.load_dotenv()
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        config = load_config(Path(args.config))
    except (OSError, ConfigError) as error:
        print(f"Config problem: {error}")
        return 2
    ledger = Ledger(config.ledger_dir)
    return args.func(args, config, ledger)


if __name__ == "__main__":
    sys.exit(main())
