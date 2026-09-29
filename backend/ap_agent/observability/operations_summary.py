"""Operations dashboard payload — KPIs, attention, breakdowns, recent runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func

from ap_agent.observability.metrics import summarise
from ap_agent.observability.trends import kpi_trends
from ap_agent.tenants import ALL_TENANTS, DEMO_TENANT_IDS

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVAL_RESULTS = _REPO_ROOT / "backend" / "evals" / "results.json"

# Cited industry references for benchmark chips (see docs/roi-framing.md).
_BENCHMARKS = {
    "touchless_rate_industry": 0.354,
    "touchless_rate_source": "Ardent Partners, State of ePayables 2025",
    "exception_resolution_days_median": 4.0,
    "exception_resolution_source": "APQC OSB measure 100632",
}


def _tenant_ids(tenant_id: str) -> list[str]:
    if tenant_id == ALL_TENANTS:
        return list(DEMO_TENANT_IDS)
    return [tenant_id]


def _scope_tenant(query: Any, model: Any, tenant_id: str) -> Any:
    if tenant_id == ALL_TENANTS:
        return query
    return query.filter(model.tenant_id == tenant_id)


def _pending_reviews_count(session: Any, *, tenant_id: str) -> int:
    from ap_agent.persistence.models import HitlReview

    q = session.query(func.count(HitlReview.id)).filter(HitlReview.status == "pending")
    if tenant_id != ALL_TENANTS:
        q = q.filter(HitlReview.tenant_id == tenant_id)
    return int(q.scalar() or 0)


def _route_breakdown(session: Any, *, tenant_id: str) -> dict[str, int]:
    """Route counts for document-processed runs only — same scope as recent runs + KPIs."""
    from ap_agent.persistence.document_runs import document_processed_runs_query
    from ap_agent.persistence.models import DecisionRow, Run

    doc_runs = document_processed_runs_query(session, tenant_id=tenant_id)
    q = (
        session.query(DecisionRow.route, func.count(DecisionRow.id))
        .join(Run, Run.run_id == DecisionRow.run_id)
        .filter(Run.run_id.in_(doc_runs.with_entities(Run.run_id)))
        .group_by(DecisionRow.route)
    )
    return {str(route): int(count) for route, count in q.all()}


def _top_exception_checks(session: Any, *, tenant_id: str, limit: int = 5) -> list[dict[str, Any]]:
    from ap_agent.persistence.models import CheckResultRow

    q = (
        _scope_tenant(
            session.query(
                CheckResultRow.name,
                func.count(func.distinct(CheckResultRow.run_id)),
            ),
            CheckResultRow,
            tenant_id,
        )
        .filter(
            (CheckResultRow.verdict == "fail") | (CheckResultRow.forces_review.is_(True)),
        )
        .group_by(CheckResultRow.name)
        .order_by(func.count(func.distinct(CheckResultRow.run_id)).desc())
        .limit(limit)
    )
    return [{"name": name, "run_count": int(count)} for name, count in q.all()]


def _eval_gate() -> dict[str, Any]:
    if not _EVAL_RESULTS.is_file():
        return {"status": "unavailable", "policy_adherence": None, "routing_label_consistency": None}
    try:
        data = json.loads(_EVAL_RESULTS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unavailable", "policy_adherence": None, "routing_label_consistency": None}
    metrics = data.get("metrics") or {}
    return {
        "status": data.get("status", "available"),
        "policy_adherence": metrics.get("policy_adherence"),
        "routing_label_consistency": metrics.get("routing_label_consistency"),
        "generated_at": data.get("generated_at"),
    }


def _run_to_summary_dict(run: Any) -> dict[str, Any]:
    """Map a persisted Run row to the queue summary JSON shape."""
    from ap_agent.observability.langfuse_export import trace_url

    invoice = run.invoice
    decision = run.decision
    vendor = getattr(invoice, "vendor_name_raw", None) or "—"
    total = str(getattr(invoice, "total_amount", "")) if invoice is not None else "—"
    route = getattr(decision, "route", None)
    pending_review = route == "route_for_approval" and (
        getattr(decision, "decided_by", None) != "human"
    )
    status = "awaiting_review" if pending_review else run.status
    tid = run.trace_id
    return {
        "run_id": run.run_id,
        "tenant_id": run.tenant_id,
        "invoice_id": run.invoice_id,
        "scenario": "upload",
        "vendor": str(vendor),
        "total": str(total) or "—",
        "status": status,
        "route": route,
        "gl_account": getattr(decision, "gl_account", None),
        "input_tokens": int(run.input_tokens or 0),
        "output_tokens": int(run.output_tokens or 0),
        "usd": float(run.cost_usd or 0.0),
        "started_at": run.started_at.isoformat() if run.started_at else "",
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "pending_review": pending_review,
        "document_stem": None,
        "trace_id": tid,
        "trace_url": trace_url(tid) if tid else None,
        "duration_ms": float(run.duration_ms) if run.duration_ms is not None else None,
    }


def _recent_runs(session: Any, *, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
    from ap_agent.persistence.document_runs import document_processed_runs_query
    from ap_agent.persistence.models import Run

    rows: list[Any] = []
    for tid in _tenant_ids(tenant_id):
        rows.extend(
            document_processed_runs_query(session, tenant_id=tid)
            .order_by(Run.started_at.desc())
            .limit(limit)
            .all()
        )
    rows.sort(key=lambda r: r.started_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return [_run_to_summary_dict(run) for run in rows[:limit]]


def build_operations_summary(session: Any, *, tenant_id: str) -> dict[str, Any]:
    """Single payload for the Operations overview page."""
    kpis = summarise(session, tenant_id=tenant_id)
    by_name = {m["name"]: m for m in kpis.get("ap_kpis", []) + kpis.get("run_metrics", [])}

    return {
        "tenant_id": tenant_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "kpis": kpis,
        "benchmarks": _BENCHMARKS,
        "attention": {
            "pending_reviews": _pending_reviews_count(session, tenant_id=tenant_id),
            "exceptions_caught": by_name.get("exceptions_caught", {}).get("value", 0),
        },
        "route_breakdown": _route_breakdown(session, tenant_id=tenant_id),
        "top_exception_checks": _top_exception_checks(session, tenant_id=tenant_id),
        "eval_gate": _eval_gate(),
        "recent_runs": _recent_runs(session, tenant_id=tenant_id),
        "trends": kpi_trends(session, tenant_id=tenant_id),
    }
