"""Daily KPI trend series for Operations sparklines."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

_DEFAULT_DAYS = 14


def _empty_bucket() -> dict[str, float]:
    return {
        "runs": 0.0,
        "decided": 0.0,
        "auto": 0.0,
        "escalated": 0.0,
        "latency_sum": 0.0,
        "latency_n": 0.0,
        "cost_sum": 0.0,
        "cost_n": 0.0,
    }


def kpi_trends(session: Any, *, tenant_id: str, days: int = _DEFAULT_DAYS) -> dict[str, Any]:
    """Per-day KPI points for the last ``days`` calendar days (UTC).

    Uses document-processed runs (same scope as headline KPIs). Days with no
    runs still appear with ``value: null`` so sparklines stay aligned.
    """
    from ap_agent.persistence.document_runs import document_processed_runs_query
    from ap_agent.persistence.models import Run

    since = datetime.now(UTC) - timedelta(days=days - 1)
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)

    buckets: dict[str, dict[str, float]] = defaultdict(_empty_bucket)
    rows = (
        document_processed_runs_query(session, tenant_id=tenant_id)
        .filter(Run.started_at >= since)
        .all()
    )

    for run in rows:
        if run.started_at is None:
            continue
        key = run.started_at.astimezone(UTC).date().isoformat()
        b = buckets[key]
        b["runs"] += 1
        if run.duration_ms is not None:
            b["latency_sum"] += float(run.duration_ms)
            b["latency_n"] += 1
        if run.cost_usd is not None:
            b["cost_sum"] += float(run.cost_usd)
            b["cost_n"] += 1
        decision = run.decision
        if decision is not None:
            b["decided"] += 1
            if decision.route == "auto_approve":
                b["auto"] += 1
            if decision.route == "route_for_approval":
                b["escalated"] += 1

    today = datetime.now(UTC).date()
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]

    def _series(
        *,
        numerator: str | None = None,
        denominator: str | None = None,
        avg_sum: str | None = None,
        avg_n: str | None = None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for day in day_keys:
            b = buckets.get(day, _empty_bucket())
            n = int(b["runs"])
            if avg_sum and avg_n:
                count = int(b[avg_n])
                value = (b[avg_sum] / count) if count else None
            elif denominator and numerator:
                denom = int(b[denominator])
                value = (b[numerator] / denom) if denom else None
            elif numerator:
                value = b[numerator] if b[numerator] else None
            else:
                value = None
            out.append({"day": day, "value": value, "n": n})
        return out

    return {
        "days": days,
        "touchless_rate": _series(numerator="auto", denominator="decided"),
        "escalation_rate": _series(numerator="escalated", denominator="decided"),
        "avg_latency_ms": _series(avg_sum="latency_sum", avg_n="latency_n"),
        "cost_per_invoice_usd": _series(avg_sum="cost_sum", avg_n="cost_n"),
    }
