"""Deterministic ERP tool selection and argument scoring."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from ap_agent.agent.supervisor import new_run_state
from ap_agent.core.canonical import Invoice, InvoiceLine
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money
from ap_agent.evaluation.tool_scores import (
    expected_erp_tool_names,
    score_tool_arguments,
    score_tool_selection,
)


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


def test_expected_tools_po_backed_with_vendor() -> None:
    assert expected_erp_tool_names(_invoice(po="PO-88")) == [
        "erp.get_purchase_order",
        "erp.get_goods_receipt",
        "erp.get_vendor",
        "erp.list_historical_bills",
    ]


def test_expected_tools_non_po_unresolved_vendor() -> None:
    assert expected_erp_tool_names(_invoice(po=None, vendor_id=None)) == [
        "erp.list_historical_bills",
    ]


def test_selection_full_score_when_required_tools_called() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po="PO-88"),
    )
    state.tool_trace = [
        {"name": "erp.get_purchase_order", "input_parameters": {"doc_number": "PO-88"}},
        {"name": "erp.get_goods_receipt", "input_parameters": {"po_doc_number": "PO-88"}},
        {"name": "erp.get_vendor", "input_parameters": {"vendor_id": "V-1001"}},
        {"name": "erp.list_historical_bills", "input_parameters": {"vendor_id": "V-1001"}},
        {"name": "erp.get_purchase_order", "input_parameters": {"doc_number": "PO-88"}},
    ]
    score, reason, actual, expected = score_tool_selection(state)
    assert score == 1.0
    assert "All required tools called" in reason
    assert actual == expected


def test_selection_penalizes_missing_required_tool() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po="PO-88"),
    )
    state.tool_trace = [
        {"name": "erp.get_purchase_order", "input_parameters": {"doc_number": "PO-88"}},
    ]
    score, reason, _actual, _expected = score_tool_selection(state)
    assert score < 1.0
    assert "Missing required tools" in reason


def test_arguments_full_score_when_vendor_unresolved_and_vendor_id_null() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po="PO-88", vendor_id=None),
    )
    state.tool_trace = [
        {"name": "erp.get_purchase_order", "input_parameters": {"doc_number": "PO-88"}},
        {"name": "erp.get_goods_receipt", "input_parameters": {"po_doc_number": "PO-88"}},
        {
            "name": "erp.list_historical_bills",
            "input_parameters": {"vendor_id": None, "since": "2025-02-15"},
        },
    ]
    score, reason, failures = score_tool_arguments(state)
    assert score == 1.0
    assert failures == []
    assert "match the invoice context" in reason


def test_arguments_fail_when_po_number_wrong() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po="PO-88", vendor_id=None),
    )
    state.tool_trace = [
        {"name": "erp.get_purchase_order", "input_parameters": {"doc_number": "PO-99"}},
        {"name": "erp.get_goods_receipt", "input_parameters": {"po_doc_number": "PO-88"}},
        {"name": "erp.list_historical_bills", "input_parameters": {"vendor_id": None}},
    ]
    score, _reason, failures = score_tool_arguments(state)
    assert score < 1.0
    assert any("erp.get_purchase_order" in item for item in failures)


def test_arguments_fail_when_vendor_resolved_but_history_missing_vendor_id() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-1",
        document_id="doc-1",
        invoice=_invoice(po=None, vendor_id="V-1001"),
    )
    state.tool_trace = [
        {"name": "erp.get_vendor", "input_parameters": {"vendor_id": "V-1001"}},
        {"name": "erp.list_historical_bills", "input_parameters": {"vendor_id": None}},
    ]
    score, _reason, failures = score_tool_arguments(state)
    assert score < 1.0
    assert any("erp.list_historical_bills" in item for item in failures)
