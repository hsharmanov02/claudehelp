"""Reads final answers out of a forecaster's free text without another model call.

Each parser returns None when the text is ambiguous; the caller then falls
back to the forecasting library's model-based parser.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Sequence

PERCENTILES = (10, 20, 40, 60, 80, 90)

_PROBABILITY = re.compile(r"Probability\s*:\s*\**\s*(\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
_NUMBER = r"(-?\d[\d,]*(?:\.\d+)?|-?\.\d+)"
_PERCENTILE_LINE = re.compile(
    r"Percentile\s*(\d{1,2})\s*:\s*\**\s*" + _NUMBER + r"(?![\d.eE])", re.IGNORECASE
)
_DATE_PERCENTILE_LINE = re.compile(
    r"Percentile\s*(\d{1,2})\s*:\s*\**\s*(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?)",
    re.IGNORECASE,
)


def parse_binary(text: str) -> float | None:
    matches = _PROBABILITY.findall(text)
    if not matches:
        return None
    value = float(matches[-1])
    if not 0 <= value <= 100:
        return None
    return value / 100


def parse_multiple_choice(text: str, options: Sequence[str]) -> dict[str, float] | None:
    """Finds the last "<option>: <number>%" line for every option.

    Longer option names are matched first so "Option A2" can't be mistaken for
    "Option A". Returns probabilities that sum to 1.
    """
    found: dict[str, float] = {}
    lines = text.splitlines()
    for option in sorted(options, key=len, reverse=True):
        pattern = re.compile(
            r"^[\s>*_\-\d.)]*(?:Option\s*[_:]?\s*)?[\"'*_]*"
            + re.escape(option)
            + r"[\"'*_]*\s*[:=\-]\s*\**\s*(\d{1,3}(?:\.\d+)?)\s*(%?)",
            re.IGNORECASE,
        )
        for line in lines:
            match = pattern.match(line)
            if match:
                value = float(match.group(1))
                found[option] = value
    if set(found) != set(options):
        return None
    total = sum(found.values())
    if total <= 0:
        return None
    # Accept both percentages (sum near 100) and decimals (sum near 1).
    if 95 <= total <= 105:
        scale = 100.0
    elif 0.95 <= total <= 1.05:
        scale = 1.0
    else:
        return None
    probabilities = {option: value / scale for option, value in found.items()}
    total = sum(probabilities.values())
    return {option: p / total for option, p in probabilities.items()}


def _last_by_percentile(pairs: list[tuple[str, str]]) -> dict[int, str]:
    latest: dict[int, str] = {}
    for percentile, value in pairs:
        latest[int(percentile)] = value
    return latest


def parse_percentiles(text: str) -> list[tuple[float, float]] | None:
    """Returns [(0.1, v10), (0.2, v20), ...] if all six are present and increasing."""
    latest = _last_by_percentile(_PERCENTILE_LINE.findall(text))
    if not all(p in latest for p in PERCENTILES):
        return None
    try:
        values = [float(latest[p].replace(",", "")) for p in PERCENTILES]
    except ValueError:
        return None
    if any(b <= a for a, b in zip(values, values[1:])):
        return None
    return [(p / 100, v) for p, v in zip(PERCENTILES, values)]


def parse_date_percentiles(text: str) -> list[tuple[float, datetime]] | None:
    latest = _last_by_percentile(_DATE_PERCENTILE_LINE.findall(text))
    if not all(p in latest for p in PERCENTILES):
        return None
    dates: list[datetime] = []
    for p in PERCENTILES:
        raw = latest[p].replace(" ", "T").rstrip("Z")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        dates.append(parsed.replace(tzinfo=timezone.utc))
    if any(b <= a for a, b in zip(dates, dates[1:])):
        return None
    return [(p / 100, d) for p, d in zip(PERCENTILES, dates)]
