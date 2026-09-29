"""Real Bedrock + DeepEval agent/tool/RAG evaluation.

No scripted models and no metric mocks: the supervisor performs live hybrid
retrieval/GL coding, then DeepEval judges the resulting state with Bedrock.
"""

from __future__ import annotations

import json

import pytest

from ap_agent.agent.supervisor import new_run_state
from ap_agent.api.supervisor_factory import build_supervisor
from ap_agent.evaluation.live import evaluate_live_state
from apfixtures.cases import build_case
from apfixtures.spec import FailureMode
from evals.policy_context import _invoice_from_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.bedrock,
    pytest.mark.eval_live,
]


def test_real_agent_tool_and_rag_metrics() -> None:
    case = build_case(FailureMode.NON_PO_SERVICES, 0)
    invoice = _invoice_from_case(case.content, case.case_id, case.tenant_id)
    state = new_run_state(
        tenant_id=case.tenant_id,
        invoice_id=case.case_id,
        document_id=f"eval-{case.case_id}",
        invoice=invoice,
    )

    result = build_supervisor(
        tenant_id=case.tenant_id,
        live=True,
        mcp=False,
    ).run(state)

    assert result.error is None
    assert result.budget is not None and result.budget.total_tokens > 0
    assert result.coding_result is not None
    assert result.coding_result.retrieval_context
    assert result.tool_trace

    scores = evaluate_live_state(result)
    assert scores.task_completion is not None
    assert scores.tool_use is not None
    assert scores.rag_grounding is not None
    for score in (
        scores.task_completion,
        scores.tool_use,
        scores.rag_grounding,
    ):
        assert 0.0 <= score <= 1.0
    detail = json.dumps(scores.details, indent=2)
    assert scores.task_completion >= 0.7, detail
    assert scores.tool_use >= 0.8, detail
    assert scores.rag_grounding >= 0.7, detail
    assert scores.details["rag"]["applicable"] is True
    assert set(scores.details["rag"]["components"]) == {
        "faithfulness",
        "answer_relevancy",
        "contextual_relevancy",
    }
