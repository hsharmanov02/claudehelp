"""Combines several forecasts of the same question into one."""

from __future__ import annotations

import math
import statistics
from typing import Mapping, Sequence

BINARY_MIN = 0.01
BINARY_MAX = 0.99


def clamp(p: float, low: float = BINARY_MIN, high: float = BINARY_MAX) -> float:
    return max(low, min(high, p))


def geo_mean_odds(probabilities: Sequence[float], extremize: float = 1.0) -> float:
    """Geometric mean of odds, optionally pushed away from 50%.

    Each input is clamped first so a single 0% or 100% can't dominate.
    """
    if not probabilities:
        raise ValueError("No probabilities to combine.")
    log_odds = [math.log(clamp(p) / (1 - clamp(p))) for p in probabilities]
    mean_log_odds = sum(log_odds) / len(log_odds) * extremize
    return clamp(1 / (1 + math.exp(-mean_log_odds)))


def median_probability(probabilities: Sequence[float]) -> float:
    if not probabilities:
        raise ValueError("No probabilities to combine.")
    return clamp(float(statistics.median(probabilities)))


def combine_binary(
    probabilities: Sequence[float], method: str = "geo_mean_odds", extremize: float = 1.0
) -> float:
    if method == "geo_mean_odds":
        return geo_mean_odds(probabilities, extremize)
    if method == "median":
        return median_probability(probabilities)
    raise ValueError(f"Unknown binary aggregation method: {method}")


def combine_multiple_choice(
    forecasts: Sequence[Mapping[str, float]], options: Sequence[str], floor: float = 0.005
) -> dict[str, float]:
    """Average each option's probability across forecasts, floor, renormalize."""
    if not forecasts:
        raise ValueError("No forecasts to combine.")
    for forecast in forecasts:
        missing = set(options) - set(forecast)
        if missing:
            raise ValueError(f"Forecast is missing options: {sorted(missing)}")
    averaged = {
        option: sum(f[option] for f in forecasts) / len(forecasts) for option in options
    }
    floored = {option: max(p, floor) for option, p in averaged.items()}
    total = sum(floored.values())
    return {option: p / total for option, p in floored.items()}
