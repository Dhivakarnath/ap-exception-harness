"""HITL queue API — durable review records as the system of record (FR-11.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/hitl", tags=["hitl"])


class HitlReviewSummary(BaseModel):
    id: str
    run_id: str
    tenant_id: str
    invoice_id: str
    reason: str
    required_tier: str | None
    channel: str
    status: str
    requested_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    gl_account: str | None = None


@router.get("/reviews")
def list_reviews(
    tenant: str = Query(default="all"),
    status: str | None = Query(default="pending"),
) -> list[HitlReviewSummary]:
    """List HITL reviews from Postgres — survives API restarts."""
    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import HitlReview
    from ap_agent.tenants import ALL_TENANTS

    with session_scope() as session:
        q = session.query(HitlReview)
        if tenant != ALL_TENANTS:
            q = q.filter(HitlReview.tenant_id == tenant)
        if status:
            q = q.filter(HitlReview.status == status)
        rows = q.order_by(HitlReview.requested_at.desc()).all()
        gl_by_run = _gl_accounts(session, [r.run_id for r in rows])
        return [_to_summary(r, gl_account=gl_by_run.get(r.run_id)) for r in rows]


@router.get("/reviews/{review_id}")
def get_review(review_id: str) -> JSONResponse:
    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import HitlReview

    with session_scope() as session:
        row = session.get(HitlReview, review_id)
        if row is None:
            return JSONResponse({"error": "review not found"}, status_code=404)
        gl_by_run = _gl_accounts(session, [row.run_id])
        return JSONResponse(
            _to_summary(row, gl_account=gl_by_run.get(row.run_id)).model_dump()
        )


def _gl_accounts(session: Any, run_ids: list[str]) -> dict[str, str | None]:
    """Proposed GL on each run's decision, so the queue can offer Edit GL."""
    from ap_agent.persistence.models import DecisionRow

    if not run_ids:
        return {}
    rows = session.query(DecisionRow).filter(DecisionRow.run_id.in_(run_ids)).all()
    return {str(row.run_id): row.gl_account for row in rows}


def _to_summary(row: Any, *, gl_account: str | None = None) -> HitlReviewSummary:
    return HitlReviewSummary(
        id=str(row.id),
        run_id=str(row.run_id),
        tenant_id=str(row.tenant_id),
        invoice_id=str(row.invoice_id),
        reason=str(row.reason),
        required_tier=row.required_tier,
        channel=str(row.channel),
        status=str(row.status),
        requested_at=_iso(row.requested_at),
        decided_at=_iso(row.decided_at) if row.decided_at else None,
        decided_by=row.decided_by,
        gl_account=gl_account,
    )


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def pending_review_run_ids(*, tenant_id: str = "retail-demo") -> set[str]:
    """Run ids with a pending HITL review — used to enrich the run queue."""
    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import HitlReview

    try:
        with session_scope() as session:
            rows = (
                session.query(HitlReview.run_id)
                .filter(HitlReview.tenant_id == tenant_id, HitlReview.status == "pending")
                .all()
            )
            return {str(r[0]) for r in rows}
    except Exception:
        return set()
