"""Run metrics and AP KPIs, computed from the persisted rows (FR-12.5, FR-12.6).

These are computed from the audit tables (`runs`, `check_results`, `decisions`,
`hitl_reviews`) rather than tracked in a separate counter, for the same reason
the check ledger is rows and not a blob: a KPI must be a query a reviewer can
re-run and verify, not a number the app asserts. Recomputing from the source of
truth means the dashboard cannot silently drift from what actually happened.

**Every number is labelled measured or illustrative** (requirements §6). A KPI
this system genuinely produces — touchless rate, escalation rate, cost per
invoice from real token counts — is `measured`. A KPI that would need data this
demo does not have — a *baseline* cycle time to compare against, an industry
benchmark — is `illustrative`, with its assumption stated, so a reviewer is
never misled into reading a demo number as a validated business result. The two
AP KPIs marked illustrative here are labelled as such precisely because claiming
them as measured would be the kind of over-statement this project refuses.

The functions take a session and a tenant/time window and return typed metric
objects; the SSE bridge and the KPI dashboard both read them, and a test asserts
the arithmetic against seeded rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MetricBasis(StrEnum):
    """Whether a number is produced from real run data or is a stated assumption."""

    MEASURED = "measured"
    """Computed from this system's own persisted rows."""
    ILLUSTRATIVE = "illustrative"
    """Depends on data the demo does not have (a baseline, a benchmark); the
    assumption is stated alongside. Never to be read as a validated result."""


@dataclass(frozen=True, slots=True)
class Metric:
    """One reported metric: its value, unit, basis, and (if illustrative) why."""

    name: str
    value: float
    unit: str
    basis: MetricBasis
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 6),
            "unit": self.unit,
            "basis": self.basis.value,
            "note": self.note,
        }


# --------------------------------------------------------------- run metrics


def _scope_tenant(query: Any, model: Any, tenant_id: str) -> Any:
    """Restrict a query to one demo tenant, or leave it unscoped for ``all``."""
    from ap_agent.tenants import ALL_TENANTS

    if tenant_id == ALL_TENANTS:
        return query
    return query.filter(model.tenant_id == tenant_id)


def run_metrics(session: Any, *, tenant_id: str) -> list[Metric]:
    """Operational metrics for a tenant's runs (FR-12.5).

    All measured: they come straight from the `runs` rows. Escalation rate is the
    fraction of decided runs that routed to a human; latency and cost are means
    over completed runs; context utilisation is the mean of the recorded
    per-run fraction.

    Run counts and run-level averages use the same document-processed scope as the
    Runs queue (invoices with extraction provenance), so headline totals cannot
    drift above what operators see in history.
    """
    from sqlalchemy import func

    from ap_agent.persistence.document_runs import document_processed_runs_query
    from ap_agent.persistence.models import DecisionRow, Run

    doc_runs = document_processed_runs_query(session, tenant_id=tenant_id)
    total_runs = doc_runs.with_entities(func.count(Run.run_id)).scalar() or 0
    if total_runs == 0:
        return [Metric("total_runs", 0.0, "count", MetricBasis.MEASURED)]

    avg_duration_ms = (
        doc_runs.with_entities(func.avg(Run.duration_ms))
        .filter(Run.duration_ms.isnot(None))
        .scalar()
    )
    avg_cost = doc_runs.with_entities(func.avg(Run.cost_usd)).scalar()
    avg_ctx = (
        doc_runs.with_entities(func.avg(Run.context_utilisation))
        .filter(Run.context_utilisation.isnot(None))
        .scalar()
    )
    escalated = (
        _scope_tenant(session.query(func.count(DecisionRow.id)), DecisionRow, tenant_id)
        .filter(DecisionRow.route == "route_for_approval")
        .scalar()
        or 0
    )
    decided = (
        _scope_tenant(session.query(func.count(DecisionRow.id)), DecisionRow, tenant_id).scalar()
        or 0
    )

    return [
        Metric("total_runs", float(total_runs), "count", MetricBasis.MEASURED),
        Metric(
            "escalation_rate",
            (escalated / decided) if decided else 0.0,
            "fraction",
            MetricBasis.MEASURED,
        ),
        Metric("avg_latency_ms", float(avg_duration_ms or 0.0), "ms", MetricBasis.MEASURED),
        Metric("avg_cost_usd", float(avg_cost or 0.0), "usd", MetricBasis.MEASURED),
        Metric(
            "avg_context_utilisation",
            float(avg_ctx or 0.0),
            "fraction",
            MetricBasis.MEASURED,
        ),
    ]


# ------------------------------------------------------------------ AP KPIs


def ap_kpis(session: Any, *, tenant_id: str) -> list[Metric]:
    """The AP KPIs an AP leader actually tracks (FR-12.6).

    Measured where the system produces the data (touchless/STP rate, cost per
    invoice, match rate, exceptions caught, early-discount capture); illustrative
    where a comparison baseline the demo lacks would be needed (cycle-time
    *reduction* against a manual baseline), with the assumption stated.
    """
    from sqlalchemy import func

    from ap_agent.persistence.models import CheckResultRow, DecisionRow, Run

    decided = (
        _scope_tenant(session.query(func.count(DecisionRow.id)), DecisionRow, tenant_id).scalar()
        or 0
    )
    metrics: list[Metric] = []

    if decided == 0:
        return [Metric("decided_invoices", 0.0, "count", MetricBasis.MEASURED)]

    auto = (
        _scope_tenant(session.query(func.count(DecisionRow.id)), DecisionRow, tenant_id)
        .filter(DecisionRow.route == "auto_approve")
        .scalar()
        or 0
    )
    # Touchless / straight-through-processing rate: the headline AP KPI.
    metrics.append(
        Metric("touchless_rate", auto / decided, "fraction", MetricBasis.MEASURED)
    )

    # Cost per invoice: mean model cost over document-processed runs.
    from ap_agent.persistence.document_runs import document_processed_runs_query

    avg_cost = (
        document_processed_runs_query(session, tenant_id=tenant_id)
        .with_entities(func.avg(Run.cost_usd))
        .scalar()
    )
    metrics.append(
        Metric("cost_per_invoice_usd", float(avg_cost or 0.0), "usd", MetricBasis.MEASURED)
    )

    # Three-way match rate: PASS over evaluated (non-SKIP) three-way-match checks.
    match_evaluated = (
        _scope_tenant(session.query(func.count(CheckResultRow.id)), CheckResultRow, tenant_id)
        .filter(
            CheckResultRow.name == "three_way_match",
            CheckResultRow.verdict != "skip",
        )
        .scalar()
        or 0
    )
    match_passed = (
        _scope_tenant(session.query(func.count(CheckResultRow.id)), CheckResultRow, tenant_id)
        .filter(
            CheckResultRow.name == "three_way_match",
            CheckResultRow.verdict == "pass",
        )
        .scalar()
        or 0
    )
    metrics.append(
        Metric(
            "match_rate",
            (match_passed / match_evaluated) if match_evaluated else 0.0,
            "fraction",
            MetricBasis.MEASURED,
        )
    )

    # Exceptions caught: distinct runs with at least one FAIL or review-forcing
    # check — the exceptions the system surfaced rather than waved through.
    exceptions_caught = (
        _scope_tenant(
            session.query(func.count(func.distinct(CheckResultRow.run_id))),
            CheckResultRow,
            tenant_id,
        )
        .filter(
            (CheckResultRow.verdict == "fail") | (CheckResultRow.forces_review.is_(True)),
        )
        .scalar()
        or 0
    )
    metrics.append(
        Metric("exceptions_caught", float(exceptions_caught), "count", MetricBasis.MEASURED)
    )

    # Early-discount capture: runs whose decision recorded a discount deadline
    # (a capturable early-payment discount was identified). Measured from the
    # decision rows; the *realised* dollar capture would need payment data this
    # system deliberately does not hold (it stops at approved-for-payment).
    discount_identified = (
        _scope_tenant(session.query(func.count(DecisionRow.id)), DecisionRow, tenant_id)
        .filter(DecisionRow.discount_deadline.isnot(None))
        .scalar()
        or 0
    )
    metrics.append(
        Metric(
            "early_discount_identified_rate",
            discount_identified / decided,
            "fraction",
            MetricBasis.MEASURED,
        )
    )

    # Cycle-time reduction is ILLUSTRATIVE: it needs a manual-baseline cycle time
    # to compare the measured automated cycle time against, which this demo does
    # not have. Stated, not fabricated.
    metrics.append(
        Metric(
            "cycle_time_reduction",
            0.0,
            "fraction",
            MetricBasis.ILLUSTRATIVE,
            note=(
                "Requires a manual-processing baseline to compare against; not "
                "available in this demo. The automated per-run latency IS measured "
                "(see avg_latency_ms) — the *reduction vs manual* is what is "
                "illustrative."
            ),
        )
    )

    return metrics


def summarise(session: Any, *, tenant_id: str) -> dict[str, Any]:
    """Both metric sets for a tenant, as a JSON-ready dict for the dashboard."""
    return {
        "tenant_id": tenant_id,
        "run_metrics": [m.as_dict() for m in run_metrics(session, tenant_id=tenant_id)],
        "ap_kpis": [m.as_dict() for m in ap_kpis(session, tenant_id=tenant_id)],
    }
