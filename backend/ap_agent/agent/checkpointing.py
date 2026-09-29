"""HITL graph checkpointing — durable pause/resume, and its lifecycle.

An interrupted run's graph state must survive a process restart: an AP invoice
can sit in a review queue for days, and if the paused state lived only in memory
a deploy or crash would strand it. So production uses a **Postgres** checkpointer
(`langgraph-checkpoint-postgres`), which persists the resumable state in the same
database as everything else. Tests and offline demos use the in-memory saver,
where cross-restart durability is moot.

Two lifecycle concerns, kept strictly separate because they have opposite
rules:

* **The graph checkpoint is disposable and TTL'd.** Once a run terminates (or is
  abandoned past a TTL), its resumable state is dead weight; `sweep_checkpoints`
  deletes those threads' checkpoint rows so they do not accumulate forever. This
  touches *only* the LangGraph checkpoint tables.

* **The review and audit rows are permanent.** `hitl_reviews` and `audit_log`
  are the system of record; they are **never** deleted or rolled off. A stale
  pending review is transitioned to `expired` (recorded, escalatable) by
  `expire_stale_reviews`, not removed — deleting audit evidence is exactly what
  an AP control system must not do.

The connection-string conversion matters: the app's `database_url` is a
SQLAlchemy DSN (`postgresql+psycopg://...`); `PostgresSaver` wants a plain libpq
DSN (`postgresql://...`), so `_libpq_dsn` strips the driver tag.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ap_agent.config import get_settings

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger("ap_agent.checkpointing")


def _libpq_dsn(database_url: str) -> str:
    """Convert a SQLAlchemy DSN to the plain libpq form PostgresSaver expects."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def make_checkpointer() -> BaseCheckpointSaver[Any] | None:
    """Return the configured checkpointer, or None when HITL pause is not wanted.

    ``postgres`` builds a durable `PostgresSaver` (running its one-time table
    setup) whose state survives a restart; ``in_memory`` returns the fast
    serde-configured in-memory saver for tests and demos. The supervisor treats
    a None checkpointer as "cannot pause" and leaves a route-for-approval
    decision standing rather than faking an interrupt.
    """
    backend = get_settings().checkpointer_backend
    if backend == "postgres":
        return _make_postgres_checkpointer()
    from ap_agent.agent.supervisor import make_inmemory_checkpointer

    result: BaseCheckpointSaver[Any] = make_inmemory_checkpointer()  # type: ignore[assignment]
    return result


def _make_postgres_checkpointer() -> BaseCheckpointSaver[Any]:
    """Build and set up a durable Postgres checkpointer.

    Uses a small connection pool so repeated runs reuse connections. ``setup()``
    is idempotent (creates the checkpoint tables if absent) and safe to call on
    every construction; it is the checkpointer's own migration.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    dsn = _libpq_dsn(get_settings().database_url)
    # autocommit + no prepared-statement caching is what PostgresSaver expects.
    pool = ConnectionPool(
        conninfo=dsn,
        max_size=4,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=True,
    )
    saver = PostgresSaver(pool)  # type: ignore[arg-type]
    saver.setup()
    return saver


# --------------------------------------------------------------- lifecycle


def sweep_checkpoints(
    saver: BaseCheckpointSaver[Any],
    *,
    thread_ids: list[str],
) -> int:
    """Delete the resumable graph checkpoints for the given threads.

    Called with the thread ids of runs that have *terminated* (or been abandoned
    past the TTL) — the caller identifies those from the `runs`/`hitl_reviews`
    tables; this function only removes the checkpoint state, never any audit row.
    Returns the number of threads swept. A saver that cannot delete a thread
    (already gone) is not an error.
    """
    swept = 0
    for thread_id in thread_ids:
        delete = getattr(saver, "delete_thread", None)
        if delete is None:  # pragma: no cover - in-memory saver in tests
            continue
        try:
            delete(thread_id)
            swept += 1
        except Exception:  # noqa: BLE001 - a missing thread is fine, log and continue
            logger.debug("checkpoint sweep: thread %s already absent", thread_id)
    return swept


def terminated_thread_ids(session: Any, *, ttl_days: int | None = None) -> list[str]:
    """Thread ids whose graph checkpoint is safe to sweep.

    A thread is sweepable when its run is no longer pausable: the `hitl_reviews`
    row for it has been decided (approved/edited/rejected/expired), or the run is
    older than the TTL and never resumed. This reads the durable audit tables to
    decide — the checkpoint is derived state, the review row is the truth.
    """
    from ap_agent.persistence.models import HitlReview

    ttl = ttl_days if ttl_days is not None else get_settings().checkpoint_ttl_days
    cutoff = datetime.now(UTC) - timedelta(days=ttl)
    rows = (
        session.query(HitlReview.thread_id)
        .filter(
            HitlReview.thread_id.isnot(None),
            # Decided reviews: the run has resumed and terminated.
            (HitlReview.status.in_(["approved", "edited", "rejected", "expired"]))
            # Or abandoned: still pending but older than the TTL.
            | (HitlReview.requested_at < cutoff),
        )
        .distinct()
    )
    return [tid for (tid,) in rows if tid]


def expire_stale_reviews(session: Any, *, expiry_days: int | None = None) -> int:
    """Transition unactioned pending reviews to ``expired`` (never delete).

    A pending review left past the tenant's expiry window is *not* removed —
    that would erase audit evidence. It is marked ``expired`` and an audit entry
    is appended, so the fact that a review lapsed is itself on the record (and a
    caller can escalate from there). Returns the number expired.
    """
    from ap_agent.persistence.models import AuditLog, HitlReview

    days = expiry_days if expiry_days is not None else get_settings().review_expiry_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stale = (
        session.query(HitlReview)
        .filter(HitlReview.status == "pending", HitlReview.requested_at < cutoff)
        .all()
    )
    for review in stale:
        review.status = "expired"
        review.decided_at = datetime.now(UTC)
        session.add(
            AuditLog(
                tenant_id=review.tenant_id,
                run_id=review.run_id,
                entity_type="hitl_review",
                entity_id=review.id,
                event="hitl_expired",
                actor="system",
                detail={
                    "reason": f"pending beyond the {days}-day review window",
                    "invoice_id": review.invoice_id,
                },
            )
        )
    return len(stale)
