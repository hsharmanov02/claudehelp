"""Summaries of the ledger for people and for the dashboard on the owner's PC."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from node.ledger import Ledger


def month_summary(ledger: Ledger, month: datetime | None = None) -> dict:
    runs = forecasts = errors = published = 0
    spend = 0.0
    by_buyer: dict[str, dict] = defaultdict(lambda: {"forecasts": 0, "errors": 0, "spend_usd": 0.0})
    last_run: dict | None = None
    for entry in ledger.entries(month):
        kind = entry.get("kind")
        buyer = entry.get("buyer", "unknown")
        if kind == "run_finished":
            runs += 1
            last_run = entry
        elif kind == "forecast":
            forecasts += 1
            by_buyer[buyer]["forecasts"] += 1
            if entry.get("published"):
                published += 1
        elif kind == "forecast_error":
            errors += 1
            by_buyer[buyer]["errors"] += 1
        elif kind == "spend":
            cost = float(entry.get("cost_usd", 0.0))
            spend += cost
            by_buyer[buyer]["spend_usd"] += cost
    return {
        "runs": runs,
        "forecasts": forecasts,
        "published": published,
        "errors": errors,
        "spend_usd": round(spend, 4),
        "by_buyer": {k: {**v, "spend_usd": round(v["spend_usd"], 4)} for k, v in by_buyer.items()},
        "last_run": last_run,
    }


def format_summary(summary: dict, budget_usd: float) -> str:
    lines = [
        f"Runs this month:      {summary['runs']}",
        f"Forecasts made:       {summary['forecasts']} ({summary['published']} published)",
        f"Errors:               {summary['errors']}",
        f"Model spend:          ${summary['spend_usd']:.2f} of ${budget_usd:.2f}",
    ]
    last = summary.get("last_run")
    if last:
        lines.append(
            f"Last run:             {last['ts']} ({last.get('forecasts_made', 0)} forecasts, "
            f"{last.get('errors', 0)} errors, ${float(last.get('cost_usd', 0)):.2f})"
        )
    return "\n".join(lines)
