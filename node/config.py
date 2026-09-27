"""Loads node.toml into typed settings."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("node.toml")

# Tournaments where bots are not allowed to compete. The node refuses them even
# if someone adds them to the config by mistake.
FORBIDDEN_TARGETS = frozenset({"metaculus_cup", "cup", "quarterly_cup"})
ALLOWED_TARGETS = frozenset({"seasonal", "minibench", "bot-testing-area"})


@dataclass(frozen=True)
class GuardSettings:
    monthly_budget_usd: float = 100.0
    max_run_cost_usd: float = 10.0
    fallback_input_price_per_mtok: float = 5.0
    fallback_output_price_per_mtok: float = 25.0


@dataclass(frozen=True)
class ModelSettings:
    forecasters: tuple[tuple[str, ...], ...] = ()
    helper: tuple[str, ...] = ()
    search: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetaculusSettings:
    targets: tuple[str, ...] = ("seasonal", "minibench")
    forecasts_per_question: int = 6
    binary_aggregation: str = "geo_mean_odds"
    extremize: float = 1.0
    mc_floor: float = 0.005
    skip_already_forecasted: bool = True


@dataclass(frozen=True)
class NodeConfig:
    ledger_dir: Path = Path("data/ledger")
    guard: GuardSettings = field(default_factory=GuardSettings)
    models: ModelSettings = field(default_factory=ModelSettings)
    metaculus: MetaculusSettings = field(default_factory=MetaculusSettings)


class ConfigError(ValueError):
    pass


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> NodeConfig:
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return parse_config(raw)


def parse_config(raw: dict) -> NodeConfig:
    node = raw.get("node", {})
    guard_raw = raw.get("guard", {})
    models_raw = raw.get("models", {})
    meta_raw = raw.get("metaculus", {})

    guard = GuardSettings(**guard_raw)
    if guard.monthly_budget_usd <= 0 or guard.max_run_cost_usd <= 0:
        raise ConfigError("Budgets must be positive numbers.")

    forecasters = tuple(tuple(slot) for slot in models_raw.get("forecasters", []))
    if not forecasters or any(not slot for slot in forecasters):
        raise ConfigError("[models].forecasters needs at least one non-empty slot.")
    models = ModelSettings(
        forecasters=forecasters,
        helper=tuple(models_raw.get("helper", [])),
        search=tuple(models_raw.get("search", [])),
    )
    if not models.helper:
        raise ConfigError("[models].helper needs at least one model.")

    targets = tuple(meta_raw.get("targets", ("seasonal", "minibench")))
    for target in targets:
        if target in FORBIDDEN_TARGETS:
            raise ConfigError(
                f"Target '{target}' is a human tournament; bots may not enter it."
            )
        if target not in ALLOWED_TARGETS:
            raise ConfigError(
                f"Unknown target '{target}'. Allowed: {sorted(ALLOWED_TARGETS)}."
            )
    metaculus = MetaculusSettings(**{**meta_raw, "targets": targets})
    if metaculus.forecasts_per_question < 1:
        raise ConfigError("[metaculus].forecasts_per_question must be at least 1.")
    if metaculus.binary_aggregation not in ("geo_mean_odds", "median"):
        raise ConfigError('binary_aggregation must be "geo_mean_odds" or "median".')
    if not 0.5 <= metaculus.extremize <= 3.0:
        raise ConfigError("extremize must be between 0.5 and 3.0.")
    if not 0 <= metaculus.mc_floor < 0.1:
        raise ConfigError("mc_floor must be between 0 and 0.1.")

    return NodeConfig(
        ledger_dir=Path(node.get("ledger_dir", "data/ledger")),
        guard=guard,
        models=models,
        metaculus=metaculus,
    )
