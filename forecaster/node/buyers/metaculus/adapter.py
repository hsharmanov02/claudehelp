"""Runs the forecaster against Metaculus and records everything in the ledger."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from forecasting_tools import ForecastReport, MetaculusClient, MetaculusQuestion
from forecasting_tools.ai_models.resource_managers.monetary_cost_manager import (
    MonetaryCostManager,
)

from node.buyers.metaculus.bot import SUPPORTED_QUESTION_TYPES, NodeForecaster
from node.config import NodeConfig
from node.costs import CappedLlm, recording_spend
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


def tournament_ids(
    targets: tuple[str, ...], client: MetaculusClient, seasonal_override: str | int | None = None
) -> list[int | str]:
    mapping = {
        "seasonal": seasonal_override or client.CURRENT_AI_COMPETITION_ID,
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
    config: NodeConfig,
    models: ResolvedModels,
    publish: bool,
    client: MetaculusClient | None,
    on_question_done=None,
) -> NodeForecaster:
    forecasters = [
        CappedLlm(model=models.with_route(name), timeout=300, allowed_tries=2)
        for name in models.forecasters
    ]
    helper = CappedLlm(model=models.with_route(models.helper), timeout=120, allowed_tries=2)
    search = (
        CappedLlm(model=models.with_route(models.search), timeout=180, allowed_tries=2)
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
        on_question_done=on_question_done,
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
        tournaments = tournament_ids(targets, self.client, self.config.metaculus.seasonal_tournament)
        for tournament in tournaments:
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

    def already_done(self, questions: list[MetaculusQuestion], publish: bool) -> set:
        """Questions the node doesn't need to forecast again.

        The ledger is the record: a live run skips questions it has published
        to, and a dry run also skips ones it has dry-run before, so repeated dry
        runs don't pay for the same forecasts again. A question Metaculus shows
        as forecast but the ledger doesn't (for example the forecast went up but
        its required reasoning comment failed) is forecast again, which posts
        both. Dry runs also trust Metaculus' flag, since they can't fix anything.
        """
        published, dry = set(), set()
        for entry in self.ledger.entries():
            if entry.get("kind") == "forecast" and entry.get("buyer") == BUYER:
                (published if entry.get("published") else dry).add(entry.get("question_id"))
        done = set()
        for question in questions:
            qid = question.id_of_question
            if qid in published or (not publish and (qid in dry or question.already_forecasted)):
                done.add(qid)
        return done

    async def run(self, mode: str, publish: bool, run_budget_usd: float) -> RunSummary:
        if mode not in ("tournament", "test"):
            raise ValueError(f"Unknown mode: {mode}")
        summary = RunSummary(mode=mode, published=publish)
        models = self.model_resolver()
        guard = self.config.guard
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

        def record_spend(cost: float, model: str) -> None:
            self.ledger.append("spend", buyer=BUYER, cost_usd=round(cost, 6), model=model)

        def record_result(question, result) -> None:
            self._record(question, result, publish, summary)

        with recording_spend(
            record_spend, guard.fallback_input_price_per_mtok, guard.fallback_output_price_per_mtok
        ), MonetaryCostManager(run_budget_usd) as run_costs:
            try:
                questions, skipped = self.collect_questions(mode)
                summary.questions_seen = len(questions) + skipped
                summary.questions_skipped = skipped
                bot = self.forecaster_factory(
                    self.config, models, publish, self.client, on_question_done=record_result
                )
                # Skipping is decided here, from the ledger, not by the library.
                skip = mode == "tournament" and bot.skip_previously_forecasted_questions
                bot.skip_previously_forecasted_questions = False
                if skip:
                    done = self.already_done(questions, publish)
                    summary.questions_skipped += sum(q.id_of_question in done for q in questions)
                    questions = [q for q in questions if q.id_of_question not in done]
                # Stop before the job's own timeout so the ledger is saved and
                # unfinished questions are picked up by the next run.
                try:
                    async with asyncio.timeout(guard.max_run_minutes * 60):
                        await bot.forecast_questions(questions, return_exceptions=True)
                except TimeoutError:
                    unfinished = len(questions) - summary.forecasts_made - len(summary.errors)
                    summary.errors.append(
                        f"Stopped at the {guard.max_run_minutes:g}-minute limit; "
                        f"{unfinished} questions left for the next run."
                    )
            finally:
                summary.cost_usd = run_costs.current_usage
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
    ) -> None:
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
            return
        summary.forecasts_made += 1
        # The cost is already in the ledger as individual "spend" entries;
        # it is repeated here only for reading.
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
            cost_usd=round(float(report.price_estimate or 0.0), 6),
            minutes=round(float(report.minutes_taken or 0.0), 2),
            partial_errors=len(report.errors or []),
        )
