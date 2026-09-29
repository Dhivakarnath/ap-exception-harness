"""Slice 11 — metrics and AP KPIs computed from persisted rows (integration).

These prove the dashboard numbers are a *query over the audit tables*, not an
app-asserted counter: seed known runs/decisions/checks, then assert the KPI
arithmetic and — just as important — that each number is labelled `measured` or
`illustrative`. The cost attribution is asserted non-zero and plausible from the
seeded token/USD values.

Needs Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from ap_agent.observability.metrics import MetricBasis, ap_kpis, run_metrics, summarise
from ap_agent.observability.operations_summary import build_operations_summary
from ap_agent.persistence.db import session_scope
from ap_agent.persistence.models import (
    CheckResultRow,
    DecisionRow,
    Document,
    Invoice,
    Run,
    Tenant,
)

pytestmark = pytest.mark.integration

_TENANT = f"kpi-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def seeded() -> Any:
    """Seed two decided runs for an isolated tenant: one auto-approved (touchless,
    with a discount deadline and a passing match) and one routed for approval
    (with a review-forcing check). Torn down after."""
    ids: dict[str, Any] = {"runs": [], "docs": [], "invoices": []}
    with session_scope() as s:
        s.add(
            Tenant(
                tenant_id=_TENANT,
                name=_TENANT,
                industry="test",
                base_currency="USD",
                active_policy_version="1.0.0",
            )
        )
        s.flush()
        for i, (route, cost) in enumerate([("auto_approve", "0.0008"), ("route_for_approval", "0.0012")]):
            doc_id = uuid.uuid4().hex
            inv_id = uuid.uuid4().hex
            run_id = uuid.uuid4().hex
            ids["docs"].append(doc_id)
            ids["invoices"].append(inv_id)
            ids["runs"].append(run_id)
            s.add(
                Document(
                    document_id=doc_id, tenant_id=_TENANT, source="upload", filename="i.pdf",
                    media_type="application/pdf", size_bytes=1, content_hash=uuid.uuid4().hex,
                    storage_path=f"mem://{doc_id}",
                )
            )
            s.flush()
            s.add(
                Invoice(
                    invoice_id=inv_id, tenant_id=_TENANT, document_id=doc_id, source="upload",
                    invoice_number=f"INV-{i}", invoice_date=date(2026, 2, 15),
                    vendor_name_raw="Datamesh", currency="USD", subtotal=500, total_amount=500,
                    header_confidence=0.99, overall_confidence=0.99,
                    field_provenance={"invoice_number": {"source": "test"}},
                )
            )
            s.add(
                Run(
                    run_id=run_id, tenant_id=_TENANT, invoice_id=inv_id, policy_version="1.0.0",
                    status="completed", started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC), duration_ms=1200.0 + i * 100,
                    input_tokens=100, output_tokens=20, cost_usd=Decimal(cost),
                    model_calls=1, tool_calls=0, context_utilisation=0.01,
                )
            )
            s.flush()
            s.add(
                DecisionRow(
                    run_id=run_id, tenant_id=_TENANT, invoice_id=inv_id, route=route,
                    decided_by="policy_engine" if route == "auto_approve" else "agent",
                    rationale="seeded", decided_at=datetime.now(UTC),
                    discount_deadline=date(2026, 2, 25) if i == 0 else None,
                )
            )
            # three_way_match: pass on run 0, and a review-forcing bank check on run 1
            s.add(
                CheckResultRow(
                    run_id=run_id, tenant_id=_TENANT, sequence=1, name="three_way_match",
                    category="matching", verdict="pass" if i == 0 else "skip",
                    severity="info", reasoning="seeded", forces_review=False,
                    evaluated_at=datetime.now(UTC),
                )
            )
            if i == 1:
                s.add(
                    CheckResultRow(
                        run_id=run_id, tenant_id=_TENANT, sequence=2, name="bank_detail_change",
                        category="identity", verdict="flag", severity="critical",
                        reasoning="seeded", forces_review=True, evaluated_at=datetime.now(UTC),
                    )
                )
    yield ids
    with session_scope() as s:
        for run_id in ids["runs"]:
            s.query(CheckResultRow).filter_by(run_id=run_id).delete(synchronize_session=False)
            s.query(DecisionRow).filter_by(run_id=run_id).delete(synchronize_session=False)
            s.query(Run).filter_by(run_id=run_id).delete(synchronize_session=False)
        for inv_id in ids["invoices"]:
            s.query(Invoice).filter_by(invoice_id=inv_id).delete(synchronize_session=False)
        for doc_id in ids["docs"]:
            s.query(Document).filter_by(document_id=doc_id).delete(synchronize_session=False)
        s.query(Tenant).filter_by(tenant_id=_TENANT).delete(synchronize_session=False)


def _by_name(metrics: list[Any]) -> dict[str, Any]:
    return {m.name: m for m in metrics}


class TestRunMetrics:
    def test_escalation_rate_and_cost_are_measured(self, seeded: Any) -> None:
        with session_scope() as s:
            m = _by_name(run_metrics(s, tenant_id=_TENANT))
        assert m["total_runs"].value == 2.0
        # One of two decisions routed for approval.
        assert m["escalation_rate"].value == pytest.approx(0.5)
        assert m["escalation_rate"].basis is MetricBasis.MEASURED
        # Cost attribution is non-zero and plausible (mean of 0.0008 and 0.0012).
        assert m["avg_cost_usd"].value == pytest.approx(0.001, abs=1e-6)
        assert m["avg_cost_usd"].value > 0


class TestApKpis:
    def test_touchless_and_match_and_exceptions(self, seeded: Any) -> None:
        with session_scope() as s:
            k = _by_name(ap_kpis(s, tenant_id=_TENANT))
        # One of two decided invoices auto-approved.
        assert k["touchless_rate"].value == pytest.approx(0.5)
        assert k["touchless_rate"].basis is MetricBasis.MEASURED
        # One three_way_match evaluated (non-skip), and it passed.
        assert k["match_rate"].value == pytest.approx(1.0)
        # Run 1 had a review-forcing check -> one exception caught.
        assert k["exceptions_caught"].value == pytest.approx(1.0)
        # Run 0 identified an early-payment discount.
        assert k["early_discount_identified_rate"].value == pytest.approx(0.5)

    def test_cycle_time_reduction_is_labelled_illustrative(self, seeded: Any) -> None:
        with session_scope() as s:
            k = _by_name(ap_kpis(s, tenant_id=_TENANT))
        # This one honestly cannot be measured without a manual baseline.
        assert k["cycle_time_reduction"].basis is MetricBasis.ILLUSTRATIVE
        assert k["cycle_time_reduction"].note is not None

    def test_summarise_returns_both_sets(self, seeded: Any) -> None:
        with session_scope() as s:
            payload = summarise(s, tenant_id=_TENANT)
        assert payload["tenant_id"] == _TENANT
        assert len(payload["run_metrics"]) > 0
        assert len(payload["ap_kpis"]) > 0
        # Every KPI carries an explicit basis label.
        assert all("basis" in m for m in payload["ap_kpis"])


class TestOperationsSummary:
    def test_build_operations_summary_payload(self, seeded: Any) -> None:
        with session_scope() as s:
            payload = build_operations_summary(s, tenant_id=_TENANT)
        assert payload["tenant_id"] == _TENANT
        assert "benchmarks" in payload
        assert payload["attention"]["exceptions_caught"] >= 1
        assert len(payload["recent_runs"]) >= 1
        assert isinstance(payload["route_breakdown"], dict)
        assert "trends" in payload
        assert payload["trends"]["days"] == 14
