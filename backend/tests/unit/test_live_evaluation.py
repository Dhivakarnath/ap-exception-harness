"""Deterministic contracts for live DeepEval scheduling and API payloads.

These tests do not call Bedrock. They prove scripted demos stay out of the
live scorecard, required ERP tools are derived from the invoice branch, and
GET /evals can render a row even when the joined invoice is missing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from ap_agent.agent.supervisor import new_run_state
from ap_agent.api.platform import _evaluation_payload
from ap_agent.core.canonical import Invoice, InvoiceLine
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money
from ap_agent.evaluation.live import _task_text, evaluate_live_state, schedule_live_evaluation
from ap_agent.evaluation.tool_scores import expected_erp_tool_names, score_tool_selection


def _ex(value: Any, confidence: float = 0.99) -> Extracted[Any]:
    return Extracted(value=value, confidence=confidence, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="USD")


def _invoice(*, po: str | None = "PO-1", vendor_id: str | None = "V-1001") -> Invoice:
    return Invoice(
        invoice_id="inv-1",
        tenant_id="retail-demo",
        document_id="doc-1",
        invoice_number=_ex("INV-100"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex("Datamesh Analytics"),
        currency=_ex("USD"),
        subtotal=_ex(_money("500.00")),
        total_amount=_ex(_money("500.00")),
        po_reference=_ex(po) if po else None,
        resolved_vendor_id=vendor_id,
        lines=(
            InvoiceLine(
                line_number=1,
                description=_ex("Analytics platform annual subscription"),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(_money("500.00")),
                line_total=_ex(_money("500.00")),
            ),
        ),
    )


def test_schedule_skips_scripted_runs_without_extraction_meta() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(),
    )
    assert state.extraction_meta is None
    assert schedule_live_evaluation(state) is False


def test_schedule_skips_pending_hitl_before_resolution() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(),
    )
    state.extraction_meta = {"model": "amazon.nova-lite-v1:0"}
    state.pending_hitl = True
    assert schedule_live_evaluation(state) is False


def test_schedule_allows_replace_after_hitl_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    from ap_agent.config import get_settings

    monkeypatch.setattr(get_settings(), "live_evals_enabled", True)
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(),
    )
    state.extraction_meta = {"model": "amazon.nova-lite-v1:0"}
    state.hitl_status = "approved"

    class _FakeQuery:
        def filter(self, *_args: object, **_kwargs: object) -> _FakeQuery:
            return self

        def one_or_none(self) -> object:
            return object()

    class _FakeSession:
        def get(self, *_args: object, **_kwargs: object) -> object:
            return object()

        def query(self, *_args: object, **_kwargs: object) -> _FakeQuery:
            return _FakeQuery()

        def delete(self, *_args: object, **_kwargs: object) -> None:
            return None

        def flush(self) -> None:
            return None

        def add(self, *_args: object, **_kwargs: object) -> None:
            return None

        def __enter__(self) -> _FakeSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        "ap_agent.persistence.db.session_scope",
        lambda: _FakeSession(),
    )
    started: dict[str, bool] = {}

    class _FakeThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            started["ok"] = True

        def start(self) -> None:
            return None

    monkeypatch.setattr("ap_agent.evaluation.live.threading.Thread", _FakeThread)
    assert schedule_live_evaluation(state) is True


def test_schedule_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from ap_agent.config import get_settings

    monkeypatch.setattr(get_settings(), "live_evals_enabled", False)
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(),
    )
    state.extraction_meta = {"model": "amazon.nova-lite-v1:0"}
    assert schedule_live_evaluation(state) is False


def test_po_backed_run_expects_po_grn_vendor_and_history() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po="PO-88"),
    )
    state.tool_trace = [
        {"name": "erp.get_purchase_order", "input_parameters": {"po_number": "PO-88"}},
        {"name": "erp.get_goods_receipt", "input_parameters": {"po_number": "PO-88"}},
        {"name": "erp.get_vendor", "input_parameters": {"vendor_id": "V-1001"}},
        {"name": "erp.list_historical_bills", "input_parameters": {"vendor_id": "V-1001"}},
        {"name": "erp.get_purchase_order", "input_parameters": {"po_number": "PO-88"}},
    ]
    score, _reason, actual, expected = score_tool_selection(state)
    assert score == 1.0
    assert actual == [
        "erp.get_purchase_order",
        "erp.get_goods_receipt",
        "erp.get_vendor",
        "erp.list_historical_bills",
    ]
    assert expected == expected_erp_tool_names(state.invoice)
    task = _task_text(state)
    assert "PO-88" in task
    assert "retrieve PO PO-88 and its goods receipt" in task
    assert "retrieve vendor V-1001" in task
    assert "do not propose a new account" in task


def test_non_po_run_does_not_expect_po_tools() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po=None),
    )
    expected = expected_erp_tool_names(state.invoice)
    assert expected == [
        "erp.get_vendor",
        "erp.list_historical_bills",
    ]
    task = _task_text(state)
    assert "non-PO" in task
    assert "retrieve PO" not in task
    assert "retrieve vendor V-1001" in task
    assert "Ground any model-proposed GL coding" in task


def test_unresolved_vendor_does_not_expect_get_vendor() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po=None, vendor_id=None),
    )
    assert expected_erp_tool_names(state.invoice) == ["erp.list_historical_bills"]
    task = _task_text(state)
    assert "retrieve vendor" not in task
    assert "search historical bills" in task


def test_evaluation_payload_survives_missing_invoice() -> None:
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    row = SimpleNamespace(
        run=SimpleNamespace(invoice=None, decision=None),
        run_id="run-1",
        tenant_id="retail-demo",
        invoice_id="inv-missing",
        status="completed",
        task_completion=0.95,
        tool_use=1.0,
        rag_grounding=None,
        details={"rag": {"applicable": False}},
        judge_model="amazon.nova-lite-v1:0",
        error=None,
        started_at=now,
        evaluated_at=now,
        created_at=now,
    )
    payload = _evaluation_payload(row)
    assert payload["invoice_number"] == "inv-missing"
    assert payload["vendor"] == "Unknown vendor"
    assert payload["route"] is None
    assert payload["metrics"] == {
        "task_completion": 0.95,
        "tool_use": 1.0,
        "rag_grounding": None,
        "extraction_accuracy": None,
        "policy_adherence": None,
    }
    assert payload["extraction_applicable"] is False
    assert payload["policy_applicable"] is False
    assert payload["breakdown"]["tools"] == {
        "selection": None,
        "arguments": None,
        "actual": [],
        "expected": [],
        "reason": None,
        "mismatches": [],
    }
    assert payload["breakdown"]["rag"] == {
        "faithfulness": None,
        "answer_relevancy": None,
        "contextual_relevancy": None,
    }
    assert payload["rag_applicable"] is False
    assert payload["is_non_po"] is None
    assert payload["evaluated_after_hitl"] is False


def test_evaluation_payload_derives_is_non_po_from_po_reference() -> None:
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    po_invoice = SimpleNamespace(po_reference="PO-12345")
    non_po_invoice = SimpleNamespace(po_reference=None)
    row_po = SimpleNamespace(
        run=SimpleNamespace(invoice=po_invoice, decision=None),
        run_id="run-po",
        tenant_id="retail-demo",
        invoice_id="inv-po",
        status="completed",
        task_completion=1.0,
        tool_use=1.0,
        rag_grounding=None,
        details={"rag": {"applicable": False}},
        judge_model="amazon.nova-lite-v1:0",
        error=None,
        started_at=now,
        evaluated_at=now,
        created_at=now,
    )
    row_non_po = SimpleNamespace(
        run=SimpleNamespace(invoice=non_po_invoice, decision=None),
        run_id="run-non-po",
        tenant_id="retail-demo",
        invoice_id="inv-non-po",
        status="completed",
        task_completion=1.0,
        tool_use=1.0,
        rag_grounding=0.9,
        details={"rag": {"applicable": True, "components": {}}},
        judge_model="amazon.nova-lite-v1:0",
        error=None,
        started_at=now,
        evaluated_at=now,
        created_at=now,
    )
    assert _evaluation_payload(row_po)["is_non_po"] is False
    assert _evaluation_payload(row_non_po)["is_non_po"] is True


def test_evaluation_payload_exposes_component_scores() -> None:
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    row = SimpleNamespace(
        run=SimpleNamespace(invoice=None, decision=None),
        run_id="run-2",
        tenant_id="retail-demo",
        invoice_id="inv-2",
        status="completed",
        task_completion=0.95,
        tool_use=0.0,
        rag_grounding=1.0,
        details={
            "tool_selection": {
                "score": 1.0,
                "actual": ["erp.list_historical_bills"],
                "expected": ["erp.list_historical_bills"],
            },
            "argument_correctness": {"score": 0.0},
            "rag": {
                "applicable": True,
                "components": {
                    "faithfulness": 1.0,
                    "answer_relevancy": 1.0,
                    "contextual_relevancy": 1.0,
                },
            },
        },
        judge_model="amazon.nova-lite-v1:0",
        error=None,
        started_at=now,
        evaluated_at=now,
        created_at=now,
    )
    payload = _evaluation_payload(row)
    assert payload["breakdown"]["tools"]["selection"] == 1.0
    assert payload["breakdown"]["tools"]["arguments"] == 0.0
    assert payload["breakdown"]["tools"]["reason"] is None
    assert payload["breakdown"]["rag"]["faithfulness"] == 1.0
    assert payload["rag_applicable"] is True


def test_evaluate_live_state_uses_deterministic_tool_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeMetric:
        reason = "task ok"

        def __init__(self, **_kwargs: object) -> None:
            return None

        def measure(self, *_args: object, **_kwargs: object) -> float:
            return 0.9

    monkeypatch.setattr("deepeval.metrics.TaskCompletionMetric", _FakeMetric)
    monkeypatch.setattr(
        "ap_agent.evaluation.deterministic_scores.score_extraction_accuracy",
        lambda _state: (None, {"applicable": False}),
    )
    monkeypatch.setattr(
        "ap_agent.evaluation.deterministic_scores.score_policy_adherence",
        lambda _state: (None, {"applicable": False}),
    )
    monkeypatch.setattr(
        "ap_agent.evaluation.judge.bedrock_judge_model",
        lambda: object(),
    )

    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po="PO-88", vendor_id=None),
    )
    state.tool_trace = [
        {"name": "erp.get_purchase_order", "input_parameters": {"doc_number": "PO-88"}},
        {"name": "erp.get_goods_receipt", "input_parameters": {"po_doc_number": "PO-88"}},
        {"name": "erp.list_historical_bills", "input_parameters": {"vendor_id": None}},
    ]

    scores = evaluate_live_state(state)
    assert scores.tool_use == 1.0
    assert scores.details["tool_selection"]["scorer"] == "deterministic"
    assert scores.details["argument_correctness"]["scorer"] == "deterministic"
    assert scores.details["tool_selection"]["score"] == 1.0
    assert scores.details["argument_correctness"]["score"] == 1.0
