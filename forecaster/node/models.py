"""Picks concrete model names from the preference lists in node.toml.

Model names change every few months. Each slot lists names newest first, and
the node uses the first one OpenRouter currently serves, so a stale config
degrades to an older model instead of failing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable

import requests

from node.config import ModelSettings

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
ROUTE_PREFIX = "openrouter/"


def fetch_openrouter_catalog(timeout: float = 20.0) -> set[str] | None:
    """Model ids OpenRouter serves right now, or None if the list can't be read."""
    try:
        response = requests.get(OPENROUTER_MODELS_URL, timeout=timeout)
        response.raise_for_status()
        return {item["id"] for item in response.json().get("data", []) if "id" in item}
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        logger.warning(f"Could not read the OpenRouter model list: {error}")
        return None


def pick(preferences: Iterable[str], catalog: set[str] | None) -> str:
    preferences = list(preferences)
    if not preferences:
        raise ValueError("Empty model preference list.")
    if catalog is None:
        # Offline: trust the first choice and let the call itself fail loudly.
        return preferences[0]
    for name in preferences:
        if name in catalog:
            return name
    logger.warning(
        f"None of {preferences} is listed by OpenRouter; using {preferences[0]}."
    )
    return preferences[0]


@dataclass(frozen=True)
class ResolvedModels:
    forecasters: tuple[str, ...]
    helper: str
    search: str | None

    def with_route(self, name: str) -> str:
        return name if name.startswith(ROUTE_PREFIX) else ROUTE_PREFIX + name


def resolve_models(
    settings: ModelSettings,
    catalog_fetcher: Callable[[], set[str] | None] = fetch_openrouter_catalog,
) -> ResolvedModels:
    catalog = catalog_fetcher()
    forecasters = tuple(pick(slot, catalog) for slot in settings.forecasters)
    helper = pick(settings.helper, catalog)
    search = pick(settings.search, catalog) if settings.search else None
    return ResolvedModels(forecasters=forecasters, helper=helper, search=search)
