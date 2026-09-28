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


# Metaculus accepts multiple-choice forecasts only when every option has at
# least 1%, so forecasts are floored to this before they are submitted.
MC_MIN = 0.01


def floor_and_normalize(
    probabilities: Mapping[str, float], floor: float = MC_MIN
) -> dict[str, float]:
    """Scales probabilities to sum to 1 with every option at or above `floor`.

    Options below the floor are set to it and the rest are scaled down to make
    room, repeating until nothing is left below the floor.
    """
    if not probabilities:
        raise ValueError("No probabilities to normalize.")
    if floor * len(probabilities) >= 1:
        raise ValueError(f"A floor of {floor} leaves no room for {len(probabilities)} options.")
    if any(p < 0 or math.isnan(p) for p in probabilities.values()):
        raise ValueError("Probabilities must be non-negative numbers.")
    floored: set[str] = set()
    while True:
        free = [k for k in probabilities if k not in floored]
        room = 1.0 - floor * len(floored)
        free_total = sum(probabilities[k] for k in free)
        if free_total <= 0:
            share = room / len(free)
            scaled = {k: share for k in free}
        else:
            scaled = {k: probabilities[k] / free_total * room for k in free}
        below = [k for k, p in scaled.items() if p < floor]
        if not below:
            break
        floored.update(below)
    return {k: (floor if k in floored else scaled[k]) for k in probabilities}


def combine_multiple_choice(
    forecasts: Sequence[Mapping[str, float]], options: Sequence[str], floor: float = MC_MIN
) -> dict[str, float]:
    """Average each option's probability across forecasts, then floor and renormalize."""
    if not forecasts:
        raise ValueError("No forecasts to combine.")
    for forecast in forecasts:
        missing = set(options) - set(forecast)
        if missing:
            raise ValueError(f"Forecast is missing options: {sorted(missing)}")
    averaged = {
        option: sum(f[option] for f in forecasts) / len(forecasts) for option in options
    }
    return floor_and_normalize(averaged, max(floor, MC_MIN))
