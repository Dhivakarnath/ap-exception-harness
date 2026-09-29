"""DeepEval RAG triad on the GL coding node (FR-13.5)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RagTriadScores:
    faithfulness: float | None
    answer_relevancy: float | None
    contextual_relevancy: float | None
    cases_run: int


def _mean(scores: list[float]) -> float | None:
    return round(sum(scores) / len(scores), 4) if scores else None


def run_rag_triad(cases: list[dict[str, Any]]) -> RagTriadScores:
    """Score pre-built RAG cases with DeepEval's triad metrics.

    Each case dict: ``input``, ``actual_output``, ``retrieval_context`` (list[str]).
    Requires AWS credentials for the judge model (same chain as Bedrock).
    """
    if not cases:
        return RagTriadScores(None, None, None, 0)

    try:
        from deepeval import evaluate
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualRelevancyMetric,
            FaithfulnessMetric,
        )
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:
        logger.warning("deepeval not installed: %s", exc)
        return RagTriadScores(None, None, None, 0)

    test_cases = [
        LLMTestCase(
            input=c["input"],
            actual_output=c["actual_output"],
            retrieval_context=c["retrieval_context"],
        )
        for c in cases
    ]
    from evals.deepeval_model import bedrock_judge_model

    judge = bedrock_judge_model()
    metrics = [
        FaithfulnessMetric(threshold=0.5, model=judge, async_mode=False),
        AnswerRelevancyMetric(threshold=0.5, model=judge, async_mode=False),
        ContextualRelevancyMetric(threshold=0.5, model=judge, async_mode=False),
    ]
    result = evaluate(test_cases=test_cases, metrics=metrics, print_results=False)

    faith_scores: list[float] = []
    ans_scores: list[float] = []
    ctx_scores: list[float] = []
    for test_result in result.test_results:
        for metric_data in test_result.metrics_data:
            if metric_data.score is None:
                continue
            name = metric_data.name or ""
            if "Faithfulness" in name:
                faith_scores.append(metric_data.score)
            elif "Answer Relevancy" in name or "Relevancy" in name and "Contextual" not in name:
                ans_scores.append(metric_data.score)
            elif "Contextual Relevancy" in name:
                ctx_scores.append(metric_data.score)

    return RagTriadScores(
        faithfulness=_mean(faith_scores),
        answer_relevancy=_mean(ans_scores),
        contextual_relevancy=_mean(ctx_scores),
        cases_run=len(cases),
    )
