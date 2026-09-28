"""The forecasting bot: research once, forecast with several models, combine.

Prompts follow Metaculus' official Fall 2026 template bot, which is a proven
baseline. The node's changes are elsewhere: a multi-vendor model ensemble,
geometric-mean-of-odds aggregation, combined research sources, and
deterministic answer parsing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel

from forecasting_tools import (
    AskNewsSearcher,
    BinaryPrediction,
    BinaryQuestion,
    DatePercentile,
    DateQuestion,
    ForecastBot,
    ForecastReport,
    GeneralLlm,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericDistribution,
    NumericQuestion,
    Percentile,
    PredictedOption,
    PredictedOptionList,
    ReasonedPrediction,
    clean_indents,
    structure_output,
)

from node.aggregate import clamp, combine_binary, combine_multiple_choice, floor_and_normalize
from node.config import MetaculusSettings
from node.parsing import (
    parse_binary,
    parse_date_percentiles,
    parse_multiple_choice,
    parse_percentiles,
)

logger = logging.getLogger(__name__)

SUPPORTED_QUESTION_TYPES = (BinaryQuestion, MultipleChoiceQuestion, NumericQuestion, DateQuestion)

QuestionResult = Callable[[MetaculusQuestion, ForecastReport | BaseException], None]


class OptionProbability(BaseModel):
    option_name: str
    probability: float


def option_list(probabilities: dict[str, float], options: list[str]) -> PredictedOptionList:
    """Builds the submission, flooring every option to Metaculus' 1% minimum first.

    The library's own validator rejects a forecast whose options move by more
    than 5 points when it applies that minimum, which happens with many 0% options.
    """
    floored = floor_and_normalize({option: probabilities[option] for option in options})
    return PredictedOptionList(
        predicted_options=[
            PredictedOption(option_name=option, probability=floored[option]) for option in options
        ]
    )


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _timing(question: MetaculusQuestion) -> str:
    parts = []
    if question.close_time:
        parts.append(f"Forecasting closes: {question.close_time:%Y-%m-%d %H:%M} UTC.")
    if question.scheduled_resolution_time:
        parts.append(
            f"Scheduled resolution: {question.scheduled_resolution_time:%Y-%m-%d}."
        )
    return " ".join(parts)


class NodeForecaster(ForecastBot):
    """ForecastBot with a rotating multi-model ensemble."""

    def __init__(
        self,
        *,
        settings: MetaculusSettings,
        forecasters: list[GeneralLlm],
        helper: GeneralLlm,
        search: GeneralLlm | None,
        use_asknews: bool,
        max_concurrent_questions: int = 2,
        on_question_done: QuestionResult | None = None,
        **kwargs,
    ) -> None:
        if not forecasters:
            raise ValueError("At least one forecaster model is required.")
        # Public names match the __init__ parameters because the library's
        # get_config() (used by benchmarks) reads them back by name.
        self.settings = settings
        self.forecasters = forecasters
        self.helper = helper
        self.search = search
        self.use_asknews = use_asknews
        self.max_concurrent_questions = max_concurrent_questions
        self._forecasters = forecasters
        self._search = search
        self._use_asknews = use_asknews
        self._rotation: dict[str, int] = {}
        self._rotation_lock = asyncio.Lock()
        self._question_slots = asyncio.Semaphore(max_concurrent_questions)
        self.on_question_done = on_question_done
        super().__init__(
            research_reports_per_question=1,
            predictions_per_research_report=settings.forecasts_per_question,
            use_research_summary_to_forecast=False,
            skip_previously_forecasted_questions=settings.skip_already_forecasted,
            extra_metadata_in_explanation=True,
            llms={
                "default": forecasters[0],
                "summarizer": helper,
                "researcher": search,
                "parser": helper,
            },
            **kwargs,
        )

    @classmethod
    def _llm_config_defaults(cls) -> dict[str, str | GeneralLlm | None]:
        # Declared so the base class doesn't warn about our purposes. The real
        # models are always passed in by the adapter.
        return {"default": None, "summarizer": None, "researcher": None, "parser": None}

    # ------------------------------------------------------------ plumbing

    async def _run_individual_question_with_error_propagation(self, question):
        async with self._question_slots:
            try:
                report = await super()._run_individual_question_with_error_propagation(question)
            except Exception as error:
                self._report_done(question, error)
                raise
            self._report_done(question, report)
            return report

    def _report_done(self, question, result) -> None:
        # Called as soon as each question finishes (after publishing), so the
        # ledger is current even if the run is stopped part way.
        if self.on_question_done is None:
            return
        try:
            self.on_question_done(question, result)
        except Exception:
            logger.exception(f"Recording the result for {question.page_url} failed.")

    async def _next_forecaster(self, question: MetaculusQuestion) -> GeneralLlm:
        key = str(question.id_of_question or question.page_url or question.question_text)
        async with self._rotation_lock:
            index = self._rotation.get(key, 0)
            self._rotation[key] = index + 1
        return self._forecasters[index % len(self._forecasters)]

    async def _ask(self, question: MetaculusQuestion, prompt: str) -> tuple[str, str]:
        llm = await self._next_forecaster(question)
        reasoning = await llm.invoke(prompt)
        logger.info(f"{llm.model} answered {question.page_url}")
        return llm.model, reasoning

    # ------------------------------------------------------------ research

    async def run_research(self, question: MetaculusQuestion) -> str:
        prompt = clean_indents(
            f"""
            You are an assistant to a superforecaster.
            The superforecaster will give you a question they intend to forecast on.
            To be a great assistant, you generate a concise but detailed rundown of the most relevant news, including if the question would resolve Yes or No based on current information.
            You do not produce forecasts yourself.

            Question:
            {question.question_text}

            This question's outcome will be determined by the specific criteria below:
            {question.resolution_criteria}

            {question.fine_print}
            """
        )
        sources: list[tuple[str, asyncio.Future]] = []
        if self._use_asknews:
            sources.append(
                (
                    "AskNews",
                    AskNewsSearcher().call_preconfigured_version(
                        "asknews/news-summaries", prompt
                    ),
                )
            )
        if self._search is not None:
            sources.append((f"Web search ({self._search.model})", self._search.invoke(prompt)))
        if not sources:
            logger.warning("No research source is configured; forecasting without research.")
            return ""

        results = await asyncio.gather(*(task for _, task in sources), return_exceptions=True)
        sections = []
        for (label, _), result in zip(sources, results):
            if isinstance(result, BaseException):
                logger.warning(f"{label} research failed for {question.page_url}: {result}")
                continue
            if result and result.strip():
                sections.append(f"## {label}\n{result.strip()}")
        if not sections:
            logger.warning(f"All research failed for {question.page_url}.")
        return "\n\n".join(sections)

    # ------------------------------------------------------------ binary

    async def _run_forecast_on_binary(
        self, question: BinaryQuestion, research: str
    ) -> ReasonedPrediction[float]:
        prompt = clean_indents(
            f"""
            You are a professional forecaster interviewing for a job.

            Your interview question is:
            {question.question_text}

            Question background:
            {question.background_info}


            This question's outcome will be determined by the specific criteria below. These criteria have not yet been satisfied:
            {question.resolution_criteria}

            {question.fine_print}


            Your research assistant says:
            {research}

            Today is {_today()}. {_timing(question)}

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The status quo outcome if nothing changed.
            (c) A brief description of a scenario that results in a No outcome.
            (d) A brief description of a scenario that results in a Yes outcome.

            You write your rationale remembering that good forecasters put extra weight on the status quo outcome since the world changes slowly most of the time.

            The last thing you write is your final answer as: "Probability: ZZ%", 0-100
            """
        )
        model, reasoning = await self._ask(question, prompt)
        probability = parse_binary(reasoning)
        if probability is None:
            parsed: BinaryPrediction = await structure_output(
                reasoning, BinaryPrediction, model=self.get_llm("parser", "llm")
            )
            probability = parsed.prediction_in_decimal
        return ReasonedPrediction(
            prediction_value=clamp(probability), reasoning=f"*Model: {model}*\n\n{reasoning}"
        )

    # ------------------------------------------------------------ multiple choice

    async def _run_forecast_on_multiple_choice(
        self, question: MultipleChoiceQuestion, research: str
    ) -> ReasonedPrediction[PredictedOptionList]:
        prompt = clean_indents(
            f"""
            You are a professional forecaster interviewing for a job.

            Your interview question is:
            {question.question_text}

            The options are: {question.options}


            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}


            Your research assistant says:
            {research}

            Today is {_today()}. {_timing(question)}

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The status quo outcome if nothing changed.
            (c) A description of an scenario that results in an unexpected outcome.

            You write your rationale remembering that (1) good forecasters put extra weight on the status quo outcome since the world changes slowly most of the time, and (2) good forecasters leave some moderate probability on most options to account for unexpected outcomes.

            The last thing you write is your final probabilities for the N options in this order {question.options} as:
            Option_A: Probability_A
            Option_B: Probability_B
            ...
            Option_N: Probability_N
            """
        )
        model, reasoning = await self._ask(question, prompt)
        probabilities = parse_multiple_choice(reasoning, question.options)
        if probabilities is None:
            probabilities = await self._parse_options_with_model(reasoning, question)
        return ReasonedPrediction(
            prediction_value=option_list(probabilities, question.options),
            reasoning=f"*Model: {model}*\n\n{reasoning}",
        )

    async def _parse_options_with_model(
        self, reasoning: str, question: MultipleChoiceQuestion
    ) -> dict[str, float]:
        # Parsed into plain numbers rather than the library's PredictedOptionList,
        # whose validator would reject answers with many 0% options before the
        # node could floor them.
        parsed: list[OptionProbability] = await structure_output(
            text_to_structure=reasoning,
            output_type=list[OptionProbability],
            model=self.get_llm("parser", "llm"),
            additional_instructions=(
                f"Make sure that all option names are one of the following: {question.options}. "
                "Give each probability as a decimal between 0 and 1. "
                "Include options with 0% probability rather than skipping them."
            ),
        )
        found = {p.option_name: p.probability for p in parsed}
        if set(found) != set(question.options) or len(parsed) != len(question.options):
            raise ValueError(f"Parsed options {sorted(found)} don't match {question.options}.")
        total = sum(found.values())
        # Decimals as asked, or percentages if the parser ignored that.
        if not (0.95 <= total <= 1.05 or 95 <= total <= 105):
            raise ValueError(f"Parsed option probabilities sum to {total}, not 1.")
        return {option: p / total for option, p in found.items()}

    # ------------------------------------------------------------ numeric and date

    def _bound_messages(self, question: NumericQuestion | DateQuestion) -> tuple[str, str]:
        if isinstance(question, NumericQuestion):
            upper = (
                question.nominal_upper_bound
                if question.nominal_upper_bound is not None
                else question.upper_bound
            )
            lower = (
                question.nominal_lower_bound
                if question.nominal_lower_bound is not None
                else question.lower_bound
            )
            unit = question.unit_of_measure or ""
        else:
            upper = question.upper_bound.date().isoformat()
            lower = question.lower_bound.date().isoformat()
            unit = ""
        if question.open_upper_bound:
            upper_message = f"The question creator thinks the number is likely not higher than {upper} {unit}."
        else:
            upper_message = f"The outcome can not be higher than {upper} {unit}."
        if question.open_lower_bound:
            lower_message = f"The question creator thinks the number is likely not lower than {lower} {unit}."
        else:
            lower_message = f"The outcome can not be lower than {lower} {unit}."
        return upper_message, lower_message

    async def _run_forecast_on_numeric(
        self, question: NumericQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_message, lower_message = self._bound_messages(question)
        prompt = clean_indents(
            f"""
            You are a professional forecaster interviewing for a job.

            Your interview question is:
            {question.question_text}

            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Units for answer: {question.unit_of_measure if question.unit_of_measure else "Not stated (please infer this)"}

            Your research assistant says:
            {research}

            Today is {_today()}. {_timing(question)}

            {lower_message}
            {upper_message}

            Formatting Instructions:
            - Please notice the units requested and give your answer in these units (e.g. whether you represent a number as 1,000,000 or 1 million).
            - Never use scientific notation.
            - Always start with a smaller number (more negative if negative) and then increase from there. The value for percentile 10 should always be less than the value for percentile 20, and so on.

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The outcome if nothing changed.
            (c) The outcome if the current trend continued.
            (d) The expectations of experts and markets.
            (e) A brief description of an unexpected scenario that results in a low outcome.
            (f) A brief description of an unexpected scenario that results in a high outcome.

            You remind yourself that good forecasters are humble and set wide 90/10 confidence intervals to account for unknown unknowns.

            The last thing you write is your final answer as:
            "
            Percentile 10: XX (lowest number value)
            Percentile 20: XX
            Percentile 40: XX
            Percentile 60: XX
            Percentile 80: XX
            Percentile 90: XX (highest number value)
            "
            """
        )
        model, reasoning = await self._ask(question, prompt)
        pairs = parse_percentiles(reasoning)
        distribution = self._fast_distribution(
            [Percentile(percentile=p, value=v) for p, v in pairs] if pairs else None, question
        )
        if distribution is None:
            percentiles = await structure_output(
                reasoning,
                list[Percentile],
                model=self.get_llm("parser", "llm"),
                additional_instructions=(
                    f'This text forecasts the numeric question "{question.question_text}". '
                    f"Give values in these units: {question.unit_of_measure}. "
                    "Turn scientific notation into plain numbers. If percentiles are not "
                    "explicitly given, do not invent them."
                ),
            )
            distribution = NumericDistribution.from_question(percentiles, question)
        return ReasonedPrediction(
            prediction_value=distribution, reasoning=f"*Model: {model}*\n\n{reasoning}"
        )

    async def _run_forecast_on_date(
        self, question: DateQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_message, lower_message = self._bound_messages(question)
        prompt = clean_indents(
            f"""
            You are a professional forecaster interviewing for a job.

            Your interview question is:
            {question.question_text}

            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Your research assistant says:
            {research}

            Today is {_today()}. {_timing(question)}

            {lower_message}
            {upper_message}

            Formatting Instructions:
            - This is a date question, and as such, the answer must be expressed in terms of dates.
            - The dates must be written in the format of YYYY-MM-DD. If hours matter, please append the date with the hour in UTC and military time: YYYY-MM-DDTHH:MM:SSZ. No other formatting is allowed.
            - Always start with a lower date chronologically and then increase from there.

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The outcome if nothing changed.
            (c) The outcome if the current trend continued.
            (d) The expectations of experts and markets.
            (e) A brief description of an unexpected scenario that results in a low outcome.
            (f) A brief description of an unexpected scenario that results in a high outcome.

            You remind yourself that good forecasters are humble and set wide 90/10 confidence intervals to account for unknown unknowns.

            The last thing you write is your final answer as:
            "
            Percentile 10: YYYY-MM-DD (oldest date)
            Percentile 20: YYYY-MM-DD
            Percentile 40: YYYY-MM-DD
            Percentile 60: YYYY-MM-DD
            Percentile 80: YYYY-MM-DD
            Percentile 90: YYYY-MM-DD (newest date)
            "
            """
        )
        model, reasoning = await self._ask(question, prompt)
        pairs = parse_date_percentiles(reasoning)
        distribution = self._fast_distribution(
            [Percentile(percentile=p, value=d.timestamp()) for p, d in pairs] if pairs else None,
            question,
        )
        if distribution is None:
            date_percentiles: list[DatePercentile] = await structure_output(
                reasoning,
                list[DatePercentile],
                model=self.get_llm("parser", "llm"),
                additional_instructions=(
                    f'This text forecasts the date question "{question.question_text}". '
                    "Assume midnight UTC if no hour is given. If percentiles are not "
                    "explicitly given, do not invent them."
                ),
            )
            percentiles = [
                Percentile(percentile=p.percentile, value=p.value.timestamp())
                for p in date_percentiles
            ]
            distribution = NumericDistribution.from_question(percentiles, question)
        return ReasonedPrediction(
            prediction_value=distribution, reasoning=f"*Model: {model}*\n\n{reasoning}"
        )

    @staticmethod
    def _fast_distribution(
        percentiles: list[Percentile] | None, question: NumericQuestion | DateQuestion
    ) -> NumericDistribution | None:
        """The distribution from the node's own parser, or None to use the model parser."""
        if percentiles is None:
            return None
        try:
            return NumericDistribution.from_question(percentiles, question)
        except Exception as error:
            # For example values in the wrong units, all outside the range.
            logger.info(f"Parsed percentiles rejected for {question.page_url}: {error}")
            return None

    # ------------------------------------------------------------ aggregation

    async def _aggregate_predictions(self, predictions, question):
        if isinstance(question, BinaryQuestion):
            return combine_binary(
                [float(p) for p in predictions],
                method=self.settings.binary_aggregation,
                extremize=self.settings.extremize,
            )
        if isinstance(question, MultipleChoiceQuestion):
            forecasts = [
                {o.option_name: o.probability for o in p.predicted_options} for p in predictions
            ]
            combined = combine_multiple_choice(
                forecasts, question.options, floor=self.settings.mc_floor
            )
            return option_list(combined, question.options)
        return await super()._aggregate_predictions(predictions, question)

    async def _run_forecast_on_conditional(self, question, research):
        raise NotImplementedError("Conditional questions are not entered by this node.")
