"""Runs the forecaster against Metaculus and records everything in the ledger."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from forecasting_tools import ForecastReport, GeneralLlm, MetaculusClient, MetaculusQuestion
from forecasting_tools.ai_models.resource_managers.monetary_cost_manager import (
    MonetaryCostManager,
)

from node.buyers.metaculus.bot import SUPPORTED_QUESTION_TYPES, NodeForecaster
from node.config import NodeConfig
from node.costs import install_fallback_tracker
from node.ledger import Ledger
from node.models import ResolvedModels, resolve_models

logger = logging.getLogger(__name__)

BUYER = "metaculus"
TEST_TOURNAMENT = "bot-testing-area"


@dataclass
class RunSummary:
    mode: str
    published: bool
    questions_seen: int = 0
    questions_skipped: int = 0
    forecasts_made: int = 0
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    @property
    def all_failed(self) -> bool:
        attempted = self.forecasts_made + len(self.errors)
        return attempted > 0 and self.forecasts_made == 0


def tournament_ids(targets: tuple[str, ...], client: MetaculusClient) -> list[int | str]:
    mapping = {
        "seasonal": client.CURRENT_AI_COMPETITION_ID,
        "minibench": client.CURRENT_MINIBENCH_ID,
        "bot-testing-area": TEST_TOURNAMENT,
    }
    return [mapping[target] for target in targets]


def asknews_configured() -> bool:
    return bool(
        (os.getenv("ASKNEWS_CLIENT_ID") and os.getenv("ASKNEWS_SECRET"))
        or os.getenv("ASKNEWS_API_KEY")
    )


def build_forecaster(
    config: NodeConfig, models: ResolvedModels, publish: bool, client: MetaculusClient
) -> NodeForecaster:
    forecasters = [
        GeneralLlm(model=models.with_route(name), timeout=300, allowed_tries=2)
        for name in models.forecasters
    ]
    helper = GeneralLlm(model=models.with_route(models.helper), timeout=120, allowed_tries=2)
    search = (
        GeneralLlm(model=models.with_route(models.search), timeout=180, allowed_tries=2)
        if models.search
        else None
    )
    return NodeForecaster(
        settings=config.metaculus,
        forecasters=forecasters,
        helper=helper,
        search=search,
        use_asknews=asknews_configured(),
        publish_reports_to_metaculus=publish,
        metaculus_client=client,
    )


class MetaculusBuyer:
    name = BUYER

    def __init__(
        self,
        config: NodeConfig,
        ledger: Ledger,
        client: MetaculusClient | None = None,
        model_resolver: Callable[[], ResolvedModels] | None = None,
        forecaster_factory: Callable[..., NodeForecaster] = build_forecaster,
    ) -> None:
        self.config = config
        self.ledger = ledger
        self.client = client or MetaculusClient()
        self.model_resolver = model_resolver or (lambda: resolve_models(config.models))
        self.forecaster_factory = forecaster_factory

    def collect_questions(self, mode: str) -> tuple[list[MetaculusQuestion], int]:
        if mode == "test":
            targets: tuple[str, ...] = ("bot-testing-area",)
        else:
            targets = self.config.metaculus.targets
        questions: list[MetaculusQuestion] = []
        seen_ids: set = set()
        skipped = 0
        for tournament in tournament_ids(targets, self.client):
            for question in self.client.get_all_open_questions_from_tournament(tournament):
                key = question.id_of_question or question.page_url
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                if not isinstance(question, SUPPORTED_QUESTION_TYPES):
                    skipped += 1
                    continue
                questions.append(question)
        return questions, skipped

    async def run(self, mode: str, publish: bool, run_budget_usd: float) -> RunSummary:
        if mode not in ("tournament", "test"):
            raise ValueError(f"Unknown mode: {mode}")
        summary = RunSummary(mode=mode, published=publish)
        models = self.model_resolver()
        self.ledger.append(
            "run_started",
            buyer=BUYER,
            mode=mode,
            publish=publish,
            run_budget_usd=round(run_budget_usd, 4),
            forecasters=list(models.forecasters),
            helper=models.helper,
            search=models.search,
            asknews=asknews_configured(),
        )
        install_fallback_tracker(
            self.config.guard.fallback_input_price_per_mtok,
            self.config.guard.fallback_output_price_per_mtok,
        )
        recorded_cost = 0.0
        with MonetaryCostManager(run_budget_usd) as run_costs:
            try:
                questions, skipped = self.collect_questions(mode)
                summary.questions_seen = len(questions) + skipped
                summary.questions_skipped = skipped
                bot = self.forecaster_factory(self.config, models, publish, self.client)
                if mode == "test":
                    # The test tournament is for checking the pipeline end to
                    # end, so re-forecast even questions seen before.
                    bot.skip_previously_forecasted_questions = False
                if bot.skip_previously_forecasted_questions:
                    already = [q for q in questions if q.already_forecasted]
                    summary.questions_skipped += len(already)
                    questions = [q for q in questions if not q.already_forecasted]
                    bot.skip_previously_forecasted_questions = False
                reports = await bot.forecast_questions(questions, return_exceptions=True)
                for question, report in zip(questions, reports):
                    recorded_cost += self._record(question, report, publish, summary)
            finally:
                summary.cost_usd = run_costs.current_usage
                unrecorded = max(0.0, summary.cost_usd - recorded_cost)
                if unrecorded > 0:
                    # Research or failed questions that produced no report still cost money.
                    self.ledger.append(
                        "spend", buyer=BUYER, cost_usd=round(unrecorded, 6), note="unattributed"
                    )
                self.ledger.append(
                    "run_finished",
                    buyer=BUYER,
                    mode=mode,
                    publish=publish,
                    questions_seen=summary.questions_seen,
                    questions_skipped=summary.questions_skipped,
                    forecasts_made=summary.forecasts_made,
                    errors=len(summary.errors),
                    cost_usd=round(summary.cost_usd, 6),
                )
        return summary

    def _record(
        self,
        question: MetaculusQuestion,
        report: ForecastReport | BaseException,
        publish: bool,
        summary: RunSummary,
    ) -> float:
        if isinstance(report, BaseException):
            message = f"{type(report).__name__}: {report}"[:500]
            summary.errors.append(f"{question.page_url}: {message}")
            self.ledger.append(
                "forecast_error",
                buyer=BUYER,
                question_id=question.id_of_question,
                url=question.page_url,
                error=message,
            )
            return 0.0
        summary.forecasts_made += 1
        cost = float(report.price_estimate or 0.0)
        self.ledger.append(
            "forecast",
            buyer=BUYER,
            question_id=question.id_of_question,
            post_id=question.id_of_post,
            url=question.page_url,
            question_type=type(question).__name__,
            tournaments=question.tournament_slugs,
            prediction=report.make_readable_prediction(report.prediction),
            published=publish,
            cost_usd=round(cost, 6),
            minutes=round(float(report.minutes_taken or 0.0), 2),
            partial_errors=len(report.errors or []),
        )
        if cost > 0:
            self.ledger.append(
                "spend", buyer=BUYER, cost_usd=round(cost, 6), question_id=question.id_of_question
            )
        return cost
