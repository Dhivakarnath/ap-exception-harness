"""Real DeepEval agent/tool/RAG metrics on live supervisor runs (FR-13.3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from apfixtures.manifest import ManifestEntry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentMetricScores:
    task_completion: float | None
    tool_correctness: float | None
    argument_correctness: float | None
    rag_faithfulness: float | None
    rag_answer_relevancy: float | None
    rag_contextual_relevancy: float | None
    cases_run: int


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def run_live_agent_metrics(
    entries: list[ManifestEntry],
    *,
    supervisor: Any | None = None,
) -> AgentMetricScores:
    """Run live supervisors and score their factual states with DeepEval."""
    try:
        from deepeval.integrations.langchain import CallbackHandler
    except ImportError:
        logger.warning("deepeval not installed")
        return AgentMetricScores(None, None, None, None, None, None, 0)

    from ap_agent.agent.supervisor import new_run_state
    from ap_agent.api.supervisor_factory import build_supervisor
    from ap_agent.evaluation.live import evaluate_live_state
    from apfixtures.cases import build_case
    from evals.policy_context import _invoice_from_case, _parse_case_index

    task_scores: list[float] = []
    tool_scores: list[float] = []
    argument_scores: list[float] = []
    faith_scores: list[float] = []
    answer_scores: list[float] = []
    context_scores: list[float] = []
    cases_run = 0

    for entry in entries[:5]:
        mode, index = _parse_case_index(entry.case_id)
        case = build_case(mode, index)
        invoice = _invoice_from_case(case.content, case.case_id, case.tenant_id)
        state = new_run_state(
            tenant_id=case.tenant_id,
            invoice_id=case.case_id,
            document_id=f"doc-{case.case_id}",
            invoice=invoice,
        )
        run_supervisor = supervisor
        if run_supervisor is None or run_supervisor._deps.tenant_id != case.tenant_id:
            run_supervisor = build_supervisor(
                tenant_id=case.tenant_id,
                live=True,
                mcp=False,
            )
        run_supervisor._deps.langchain_callbacks = [CallbackHandler(thread_id=state.run_id)]
        result = run_supervisor.run(state)
        scores = evaluate_live_state(result)
        cases_run += 1
        if scores.task_completion is not None:
            task_scores.append(scores.task_completion)
        tool_selection = scores.details.get("tool_selection") or {}
        argument = scores.details.get("argument_correctness") or {}
        if tool_selection.get("score") is not None:
            tool_scores.append(float(tool_selection["score"]))
        if argument.get("score") is not None:
            argument_scores.append(float(argument["score"]))
        rag = scores.details.get("rag") or {}
        components = rag.get("components") or {}
        if components:
            faith_scores.append(float(components["faithfulness"]))
            answer_scores.append(float(components["answer_relevancy"]))
            context_scores.append(float(components["contextual_relevancy"]))

    return AgentMetricScores(
        task_completion=_mean(task_scores),
        tool_correctness=_mean(tool_scores),
        argument_correctness=_mean(argument_scores),
        rag_faithfulness=_mean(faith_scores),
        rag_answer_relevancy=_mean(answer_scores),
        rag_contextual_relevancy=_mean(context_scores),
        cases_run=cases_run,
    )
