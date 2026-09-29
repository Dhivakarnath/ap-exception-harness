"""Deterministic ERP tool selection and argument scoring for live evaluations.

The supervisor graph calls ERP reads directly (not via an LLM tool loop), so tool
use is graded against the same branch rules the graph uses — not an LLM judge
that might expect a ``vendor_id`` the invoice never resolved.
"""

from __future__ import annotations

from typing import Any

from ap_agent.agent.state import RunState
from ap_agent.core.canonical import Invoice

# Parameter aliases recorded by ``TracedErpClient`` and older traces.
_PO_KEYS = frozenset({"doc_number", "po_number", "po_doc_number"})


def expected_erp_tool_names(invoice: Invoice | None) -> list[str]:
    """Required ERP read tools for this invoice branch (stable order)."""
    if invoice is None:
        return ["erp.list_historical_bills"]
    names: list[str] = []
    if not invoice.is_non_po:
        names.extend(["erp.get_purchase_order", "erp.get_goods_receipt"])
    if invoice.resolved_vendor_id:
        names.append("erp.get_vendor")
    names.append("erp.list_historical_bills")
    return names


def _calls_by_name(tool_trace: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for call in tool_trace:
        name = call.get("name")
        if not name:
            continue
        params = call.get("input_parameters")
        if not isinstance(params, dict):
            params = {}
        grouped.setdefault(str(name), []).append(params)
    return grouped


def _param_value(params: dict[str, object], keys: frozenset[str]) -> object | None:
    for key in keys:
        if key in params:
            return params[key]
    return None


def _normalized_po(params: dict[str, object]) -> str | None:
    value = _param_value(params, _PO_KEYS)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_vendor_id(params: dict[str, object]) -> str | None:
    if "vendor_id" not in params:
        return None
    value = params.get("vendor_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _any_call_matches(
    calls: list[dict[str, object]],
    *,
    predicate: Any,
) -> bool:
    return any(predicate(params) for params in calls)


def score_tool_selection(state: RunState) -> tuple[float, str, list[str], list[str]]:
    """Whether every required ERP tool was called at least once."""
    expected = expected_erp_tool_names(state.invoice)
    actual = list(
        dict.fromkeys(
            str(call["name"])
            for call in state.tool_trace
            if call.get("name")
        )
    )
    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set)
    if not missing:
        reason = (
            f"All required tools called: {', '.join(expected)}."
            if expected
            else "No ERP tools required."
        )
        return 1.0, reason, actual, expected

    score = round(len(expected_set & actual_set) / len(expected_set), 4)
    reason = f"Missing required tools: {', '.join(missing)}."
    return score, reason, actual, expected


def score_tool_arguments(state: RunState) -> tuple[float, str, list[str]]:
    """Whether recorded ERP calls used identifiers consistent with the invoice."""
    invoice = state.invoice
    if invoice is None:
        return 1.0, "No invoice on run; ERP argument checks skipped.", []

    grouped = _calls_by_name(state.tool_trace)
    checks: list[tuple[str, bool, str]] = []

    po_reference = (
        str(invoice.po_reference.value).strip()
        if invoice.po_reference is not None
        else None
    )
    vendor_id = invoice.resolved_vendor_id

    if not invoice.is_non_po and po_reference:
        po_calls = grouped.get("erp.get_purchase_order", [])
        checks.append(
            (
                "erp.get_purchase_order",
                _any_call_matches(
                    po_calls,
                    predicate=lambda p: _normalized_po(p) == po_reference,
                ),
                f"expected PO {po_reference}",
            )
        )
        grn_calls = grouped.get("erp.get_goods_receipt", [])
        checks.append(
            (
                "erp.get_goods_receipt",
                _any_call_matches(
                    grn_calls,
                    predicate=lambda p: _normalized_po(p) == po_reference,
                ),
                f"expected PO {po_reference}",
            )
        )

    history_calls = grouped.get("erp.list_historical_bills", [])
    if vendor_id:
        checks.append(
            (
                "erp.get_vendor",
                _any_call_matches(
                    grouped.get("erp.get_vendor", []),
                    predicate=lambda p: _normalized_vendor_id(p) == vendor_id,
                ),
                f"expected vendor_id {vendor_id}",
            )
        )
        checks.append(
            (
                "erp.list_historical_bills",
                _any_call_matches(
                    history_calls,
                    predicate=lambda p: _normalized_vendor_id(p) == vendor_id,
                ),
                f"expected vendor_id {vendor_id}",
            )
        )
    else:
        checks.append(
            (
                "erp.list_historical_bills",
                _any_call_matches(
                    history_calls,
                    predicate=lambda p: _normalized_vendor_id(p) is None,
                ),
                "vendor unresolved — vendor_id should be null or omitted",
            )
        )

    failures = [f"{name}: {detail}" for name, ok, detail in checks if not ok]
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    score = round(passed / total, 4) if total else 1.0
    if failures:
        reason = "; ".join(failures)
    else:
        reason = "All ERP call arguments match the invoice context."
    return score, reason, failures
