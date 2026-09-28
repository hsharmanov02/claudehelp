"""Reads final answers out of a forecaster's free text without another model call.

The prompts ask the model to end with its final answer in a fixed format, so
these parsers read only the end of the text: the last few non-empty lines must
be exactly that answer. Anything else (units such as "million" or "$", extra
words, a final answer followed by more text) returns None, and the caller falls
back to the forecasting library's model-based parser, which understands units.
A wrong fast answer is worse than a slower right one.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Sequence

PERCENTILES = (10, 20, 40, 60, 80, 90)

# Lines that carry no content: blank, code fences, stray quotes, rules.
_DECORATION = re.compile(r"^[\s`\"'*_=~\-]*$")
_OPEN = r"^[\s>#*_\"'`]*(?:[-•]\s+)?[\s*_\"'`]*"
_CLOSE = r"[\s*_\"'`.]*$"
# 1234, 1,234,567 and 12.5; no units, words, letters or scientific notation.
_NUMBER = r"(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"

_PROBABILITY_LINE = re.compile(
    _OPEN + r"Probability[*_]*\s*:\s*[*_]*\s*(\d{1,3}(?:\.\d+)?)\s*%" + _CLOSE,
    re.IGNORECASE,
)
_PERCENTILE_LINE = re.compile(
    _OPEN
    + r"Percentile\s*(\d{1,2})[*_]*\s*:\s*[*_]*\s*"
    + _NUMBER
    + r"[*_]*\s*(?:\((?:lowest|highest) number value\))?"
    + _CLOSE,
    re.IGNORECASE,
)
_DATE_PERCENTILE_LINE = re.compile(
    _OPEN
    + r"Percentile\s*(\d{1,2})[*_]*\s*:\s*[*_]*\s*"
    + r"(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?Z?)?)"
    + r"[*_]*\s*(?:\((?:oldest|newest) date\))?"
    + _CLOSE,
    re.IGNORECASE,
)


def _final_lines(text: str, count: int) -> list[str] | None:
    """The last `count` lines that carry content, or None if there are fewer."""
    lines = [line for line in text.splitlines() if not _DECORATION.match(line)]
    if len(lines) < count:
        return None
    return lines[-count:]


def parse_binary(text: str) -> float | None:
    lines = _final_lines(text, 1)
    if lines is None:
        return None
    match = _PROBABILITY_LINE.match(lines[0])
    if not match:
        return None
    value = float(match.group(1))
    if not 0 <= value <= 100:
        return None
    return value / 100


def _option_pattern(option: str) -> re.Pattern:
    return re.compile(
        r"^\s*(?:(?:[-*•>]|\d+[.)])\s+)?"  # an optional list marker, then a space
        r"[*_\"'`]*(?:Option[_\s]*)?[*_\"'`]*"
        + re.escape(option)
        + r"[*_\"'`]*\s*:\s*[*_]*\s*(\d{1,3}(?:\.\d+)?)\s*(%?)"
        + _CLOSE,
        re.IGNORECASE,
    )


def parse_multiple_choice(text: str, options: Sequence[str]) -> dict[str, float] | None:
    """Reads the final "<option>: <probability>" block, one line per option.

    The last len(options) content lines must each name exactly one option, and
    together name every option once. Returns probabilities that sum to 1.
    """
    if not options:
        return None
    lines = _final_lines(text, len(options))
    if lines is None:
        return None
    patterns = {option: _option_pattern(option) for option in options}
    found: dict[str, float] = {}
    for line in lines:
        matches = [(o, m) for o, p in patterns.items() if (m := p.match(line))]
        if len(matches) != 1:
            return None
        option, match = matches[0]
        if option in found:
            return None
        found[option] = float(match.group(1))
    if set(found) != set(options):
        return None
    total = sum(found.values())
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


def _final_percentile_block(pattern: re.Pattern, text: str) -> list[str] | None:
    lines = _final_lines(text, len(PERCENTILES))
    if lines is None:
        return None
    values = []
    for expected, line in zip(PERCENTILES, lines):
        match = pattern.match(line)
        if not match or int(match.group(1)) != expected:
            return None
        values.append(match.group(2))
    return values


def parse_percentiles(text: str) -> list[tuple[float, float]] | None:
    """Returns [(0.1, v10), (0.2, v20), ...] if the answer ends with all six, increasing."""
    raw = _final_percentile_block(_PERCENTILE_LINE, text)
    if raw is None:
        return None
    values = [float(v.replace(",", "")) for v in raw]
    if any(b <= a for a, b in zip(values, values[1:])):
        return None
    return [(p / 100, v) for p, v in zip(PERCENTILES, values)]


def parse_date_percentiles(text: str) -> list[tuple[float, datetime]] | None:
    raw = _final_percentile_block(_DATE_PERCENTILE_LINE, text)
    if raw is None:
        return None
    dates: list[datetime] = []
    for value in raw:
        try:
            parsed = datetime.fromisoformat(value.rstrip("Z"))
        except ValueError:
            return None
        dates.append(parsed.replace(tzinfo=timezone.utc))
    if any(b <= a for a, b in zip(dates, dates[1:])):
        return None
    return [(p / 100, d) for p, d in zip(PERCENTILES, dates)]
