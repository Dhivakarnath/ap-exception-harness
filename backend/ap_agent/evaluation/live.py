"""Asynchronous live scoring for real document runs.

Tool selection and argument checks are deterministic (``tool_scores``) because
the supervisor calls ERP reads in code, not via an LLM tool loop. DeepEval
(Bedrock judge) is used for task completion and RAG grounding only.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ap_agent.agent.state import RunState


@dataclass(slots=True)
class LiveScores:
    task_completion: float | None
    tool_use: float | None
    rag_grounding: float | None
    extraction_accuracy: float | None
    policy_adherence: float | None
    details: dict[str, Any]


def _tools_for_task_case(state: RunState) -> list[Any]:
    """ERP calls for DeepEval task completion (observability context only)."""
    from deepeval.test_case import ToolCall

    return [
        ToolCall(
            name=str(call["name"]),
            input_parameters=(
                call["input_parameters"] if isinstance(call.get("input_parameters"), dict) else {}
            ),
        )
        for call in state.tool_trace
        if call.get("name")
    ]


def _task_text(state: RunState) -> str:
    invoice = state.invoice
    if invoice is None:
        return "Process the invoice and produce a policy-compliant AP decision."
    branch = "non-PO" if invoice.is_non_po else "PO-backed"
    po_reference = str(invoice.po_reference.value) if invoice.po_reference is not None else "none"
    retrievals: list[str] = []
    if not invoice.is_non_po:
        retrievals.append(f"retrieve PO {po_reference} and its goods receipt")
    if invoice.resolved_vendor_id:
        retrievals.append(f"retrieve vendor {invoice.resolved_vendor_id}")
    retrievals.append("search historical bills for duplicate detection")
    retrieval_clause = ", ".join(retrievals)
    coding_clause = (
        " Ground any model-proposed GL coding in retrieved policy."
        if invoice.is_non_po
        else " Inherit GL coding from the PO; do not propose a new account."
    )
    return (
        f"Process {branch} invoice {invoice.invoice_number.value} dated "
        f"{invoice.invoice_date.value} for vendor "
        f"{invoice.resolved_vendor_id or 'unresolved'} ({invoice.vendor_name.value}), "
        f"total {invoice.total_amount.value}, PO reference {po_reference}. "
        f"Under tenant policy, {retrieval_clause}. Run deterministic checks and "
        f"produce a safe routing decision.{coding_clause}"
    )


def _outcome_text(state: RunState) -> str:
    decision = state.decision
    checks = ", ".join(f"{item.name}={item.verdict.value}" for item in state.ledger.results)
    if state.error:
        return f"Run failed loudly with {state.error}. Checks: {checks or 'none'}."
    if decision is None:
        return f"No terminal decision. Checks: {checks or 'none'}."
    coding = state.gl_coding
    coding_text = (
        f" GL={coding.gl_account}, citations={list(coding.citations)}."
        if coding is not None
        else ""
    )
    return (
        f"Route={decision.route.value}; actor={decision.decided_by.value}; "
        f"rationale={decision.rationale}.{coding_text} Checks: {checks}."
    )


def evaluate_live_state(state: RunState) -> LiveScores:
    """Run deterministic graders and DeepEval metrics over one completed live state."""
    from ap_agent.evaluation.deterministic_scores import (
        score_extraction_accuracy,
        score_policy_adherence,
    )

    extraction_accuracy, extraction_details = score_extraction_accuracy(state)
    policy_adherence, policy_details = score_policy_adherence(state)

    from deepeval.metrics import (
        AnswerRelevancyMetric,
        ContextualRelevancyMetric,
        FaithfulnessMetric,
        TaskCompletionMetric,
    )
    from deepeval.test_case import LLMTestCase

    from ap_agent.evaluation.judge import bedrock_judge_model
    from ap_agent.evaluation.tool_scores import score_tool_arguments, score_tool_selection

    judge = bedrock_judge_model()
    task = _task_text(state)
    outcome = _outcome_text(state)

    task_case = LLMTestCase(
        input=task,
        actual_output=outcome,
        tools_called=_tools_for_task_case(state),
    )
    task_metric = TaskCompletionMetric(
        task=task,
        model=judge,
        async_mode=False,
        threshold=0.7,
    )
    task_score = float(task_metric.measure(task_case, _show_indicator=False))

    selection_score, selection_reason, actual_tools, expected_tools = score_tool_selection(state)
    argument_score, argument_reason, argument_failures = score_tool_arguments(state)
    tool_use = min(selection_score, argument_score)

    details: dict[str, Any] = {
        "task_completion": {
            "score": task_score,
            "reason": task_metric.reason,
        },
        "tool_selection": {
            "score": selection_score,
            "reason": selection_reason,
            "actual": actual_tools,
            "expected": expected_tools,
            "scorer": "deterministic",
        },
        "argument_correctness": {
            "score": argument_score,
            "reason": argument_reason,
            "failures": argument_failures,
            "scorer": "deterministic",
        },
        "rag": {"applicable": False},
        "extraction": extraction_details,
        "policy": policy_details,
    }

    rag_grounding: float | None = None
    coding = state.coding_result
    if coding is not None and coding.retrieval_context:
        invoice = state.invoice
        line_descriptions = (
            "; ".join(str(line.description.value) for line in invoice.lines)
            if invoice is not None
            else "unknown"
        )
        rag_query = (
            "Select the correct GL account for this non-PO purchase using the "
            f"retrieved accounting policy. Vendor: "
            f"{invoice.vendor_name.value if invoice is not None else 'unknown'}. "
            f"Line descriptions: {line_descriptions}. "
            f"Total: {invoice.total_amount.value if invoice is not None else 'unknown'}."
        )
        rag_case = LLMTestCase(
            input=rag_query,
            actual_output=(
                f"GL {coding.coding.gl_account}: {coding.coding.reasoning or coding.reasoning}."
                if coding.coding is not None
                else coding.reasoning
            ),
            retrieval_context=list(coding.retrieval_context),
        )
        rag_metrics = [
            FaithfulnessMetric(model=judge, async_mode=False, threshold=0.75),
            AnswerRelevancyMetric(model=judge, async_mode=False, threshold=0.75),
            ContextualRelevancyMetric(model=judge, async_mode=False, threshold=0.7),
        ]
        rag_scores: dict[str, float] = {}
        rag_reasons: dict[str, str | None] = {}
        for key, metric in zip(
            ("faithfulness", "answer_relevancy", "contextual_relevancy"),
            rag_metrics,
            strict=True,
        ):
            rag_scores[key] = float(metric.measure(rag_case, _show_indicator=False))
            rag_reasons[key] = metric.reason
        # A strong average must not hide one unsafe RAG dimension.
        rag_grounding = min(rag_scores.values())
        details["rag"] = {
            "applicable": True,
            "score": rag_grounding,
            "components": rag_scores,
            "reasons": rag_reasons,
        }

    if state.hitl_status is not None:
        details["scheduling"] = {
            "after_hitl": True,
            "hitl_status": state.hitl_status,
        }

    return LiveScores(
        task_completion=task_score,
        tool_use=tool_use,
        rag_grounding=rag_grounding,
        extraction_accuracy=extraction_accuracy,
        policy_adherence=policy_adherence,
        details=details,
    )


def schedule_live_evaluation(state: RunState) -> bool:
    """Persist a pending row and evaluate a real document run off-request.

    Skips while the run is paused for HITL. After a human resolves HITL, the
    terminal resume replaces any prior evaluation row so scores reflect the
    final outcome.
    """
    if state.pending_hitl or state.invoice is None or state.extraction_meta is None:
        return False

    from ap_agent.config import get_settings

    if not get_settings().live_evals_enabled:
        return False

    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import Run, RunEvaluation

    replace_existing = state.hitl_status is not None

    try:
        with session_scope() as session:
            if session.get(Run, state.run_id) is None:
                return False
            existing = (
                session.query(RunEvaluation)
                .filter(RunEvaluation.run_id == state.run_id)
                .one_or_none()
            )
            if existing is not None and not replace_existing:
                return False
            if existing is not None:
                session.delete(existing)
                session.flush()
            session.add(
                RunEvaluation(
                    run_id=state.run_id,
                    tenant_id=state.tenant_id,
                    invoice_id=state.invoice_id,
                    status="pending",
                    judge_model=get_settings().bedrock_model_id,
                )
            )
    except Exception:
        return False

    threading.Thread(
        target=_evaluate_worker,
        args=(state,),
        daemon=True,
        name=f"eval-{state.run_id}",
    ).start()
    return True


def _evaluate_worker(state: RunState) -> None:
    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import RunEvaluation

    with session_scope() as session:
        row = session.query(RunEvaluation).filter(RunEvaluation.run_id == state.run_id).one()
        row.status = "running"
        row.started_at = datetime.now(UTC)

    try:
        scores = evaluate_live_state(state)
    except Exception as exc:  # noqa: BLE001 - persisted visibly, never hidden
        with session_scope() as session:
            row = session.query(RunEvaluation).filter(RunEvaluation.run_id == state.run_id).one()
            row.status = "failed"
            row.error = f"{type(exc).__name__}: {exc}"[:4000]
            row.evaluated_at = datetime.now(UTC)
        return

    with session_scope() as session:
        row = session.query(RunEvaluation).filter(RunEvaluation.run_id == state.run_id).one()
        row.status = "completed"
        row.task_completion = scores.task_completion
        row.tool_use = scores.tool_use
        row.rag_grounding = scores.rag_grounding
        row.extraction_accuracy = scores.extraction_accuracy
        row.policy_adherence = scores.policy_adherence
        row.details = scores.details
        row.evaluated_at = datetime.now(UTC)
