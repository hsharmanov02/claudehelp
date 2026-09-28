"""Safety checks that run before the node does any paid or public work."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from node.config import GuardSettings
from node.ledger import Ledger

KILL_FILE = Path("KILL")
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str
    run_budget_usd: float = 0.0


def kill_switch_on(kill_file: Path = KILL_FILE) -> bool:
    return env_flag("NODE_KILL") or kill_file.exists()


def check_can_run(
    guard: GuardSettings, ledger: Ledger, kill_file: Path = KILL_FILE
) -> GuardDecision:
    if kill_switch_on(kill_file):
        return GuardDecision(False, "Kill switch is on (NODE_KILL or a KILL file).")
    spent = ledger.spend_for_month()
    left = guard.monthly_budget_usd - spent
    if left <= 0:
        return GuardDecision(
            False,
            f"Monthly budget used up: ${spent:.2f} of ${guard.monthly_budget_usd:.2f}.",
        )
    run_budget = min(guard.max_run_cost_usd, left)
    return GuardDecision(True, f"${left:.2f} left this month.", run_budget)


def publishing_enabled(cli_publish: bool) -> bool:
    """Forecasts go public only when the run asks for it AND the owner has
    switched publishing on with NODE_PUBLISH=true. Either one alone is a dry run."""
    return cli_publish and env_flag("NODE_PUBLISH")
