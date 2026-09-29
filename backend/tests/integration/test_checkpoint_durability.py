"""Slice 10 hardening — durable HITL checkpointing and lifecycle (integration).

The unit HITL tests prove pause/resume within one process (in-memory saver).
These prove the production property the in-memory saver cannot: a paused run's
state is **durable across a process restart**, and the checkpoint lifecycle is
managed without ever touching the audit rows.

Three claims, each a separate incident risk if wrong:

* **Cross-process resume.** A run paused by one `Supervisor`+`PostgresSaver`
  resumes correctly through a *brand-new* `Supervisor`+`PostgresSaver` (the old
  ones destroyed) — i.e. the state survived in Postgres, not memory.
* **Sweep removes only checkpoints.** `sweep_checkpoints` deletes a terminated
  thread's resumable state but leaves its `HitlReview`/`AuditLog` rows intact.
* **Reviews expire, they do not vanish.** `expire_stale_reviews` transitions a
  stale pending review to `expired` and records an audit entry — it never
  deletes the review, because that would erase audit evidence.

Needs Postgres. The PostgresSaver's own tables are created by its idempotent
`setup()` (run in the factory).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ap_agent.agent.checkpointing import (
    _make_postgres_checkpointer,
    expire_stale_reviews,
    sweep_checkpoints,
    terminated_thread_ids,
)
from ap_agent.agent.erp_client import InProcessErpClient
from ap_agent.agent.hitl import (
    ReviewCard,
    ReviewDecision,
    ReviewDecisionType,
    persist_pending_review,
    record_review_decision,
)
from ap_agent.agent.supervisor import Supervisor, SupervisorDeps, new_run_state
from ap_agent.core.canonical import Invoice, InvoiceLine, Route
from ap_agent.core.policy_pack import load_policy_pack
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money
from ap_agent.persistence.db import session_scope
from ap_agent.persistence.models import AuditLog, HitlReview
from ap_agent.rag.coding import RawGLProposal, ScriptedCodingModel
from tests.unit.rag_fakes import make_chunk

pytestmark = pytest.mark.integration

_POLICY_PACKS = Path(__file__).resolve().parents[2] / "policy_packs"


def _ex(v: Any) -> Extracted[Any]:
    return Extracted(value=v, confidence=0.99, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(a: str) -> Money:
    return Money(amount=Decimal(a), currency="USD")


def _invoice() -> Invoice:
    return Invoice(
        invoice_id="i-dur",
        tenant_id="retail-demo",
        document_id="d",
        invoice_number=_ex("INV-DUR"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex("Datamesh"),
        currency=_ex("USD"),
        subtotal=_ex(_money("5000.00")),
        total_amount=_ex(_money("5000.00")),
        po_reference=None,
        resolved_vendor_id="V-1001",
        lines=(
            InvoiceLine(
                line_number=1,
                description=_ex("Analytics subscription"),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(_money("5000.00")),
                line_total=_ex(_money("5000.00")),
            ),
        ),
    )


class _R:
    def retrieve(self, s: Any, **k: Any) -> list[Any]:
        return [
            make_chunk(
                citation_ref="policy/gl_coding_policy_software_subscriptions.md#1",
                content="SaaS 7200",
                lexical_rank=1,
                rrf_score=0.05,
            )
        ]


@contextmanager
def _fs() -> Any:
    yield None


def _deps() -> SupervisorDeps:
    return SupervisorDeps(
        tenant_id="retail-demo",
        policy=load_policy_pack(_POLICY_PACKS / "retail_non_po.yaml"),
        erp=InProcessErpClient(),
        retriever=_R(),
        coding_model=ScriptedCodingModel(
            RawGLProposal(
                gl_account="7200",
                confidence=0.95,
                citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",),
            )
        ),
        session_factory=_fs,
        checkpointer=_make_postgres_checkpointer(),
    )


class TestCrossProcessDurability:
    def test_paused_run_resumes_through_a_fresh_supervisor_and_saver(self) -> None:
        run_id = "dur-" + uuid.uuid4().hex[:10]

        # Process 1: pause, then discard the supervisor + its saver.
        sup1 = Supervisor(_deps())
        state = new_run_state(
            tenant_id="retail-demo", invoice_id="i-dur", document_id="d", invoice=_invoice()
        )
        state.run_id = run_id
        paused = sup1.run(state)
        assert paused.pending_hitl is True
        del sup1

        # Process 2: a brand-new supervisor + brand-new PostgresSaver resumes the
        # same thread purely from what Postgres persisted.
        sup2 = Supervisor(_deps())
        done = sup2.resume(
            run_id,
            ReviewDecision(decision=ReviewDecisionType.APPROVE, approver_identity="alice@controller"),
        )
        assert done.decision is not None
        assert done.decision.route is Route.ROUTE_FOR_APPROVAL
        assert done.decision.approver_identity == "alice@controller"
        assert done.hitl_status == "approved"

        # Clean up this thread's checkpoint.
        sweep_checkpoints(sup2._deps.checkpointer, thread_ids=[run_id])  # type: ignore[arg-type]


class TestCheckpointSweepLeavesAuditRows:
    def test_sweep_removes_checkpoint_but_not_review_or_audit(self) -> None:
        saver = _make_postgres_checkpointer()
        run_id = "sweep-" + uuid.uuid4().hex[:10]

        # A minimal checkpoint write for the thread (a real run would write more).
        cfg = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": uuid.uuid4().hex,
            "ts": datetime.now(UTC).isoformat(),
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        }
        saver.put(cfg, checkpoint, {}, {})  # type: ignore[arg-type]
        assert saver.get_tuple(cfg) is not None  # checkpoint exists

        # An independent review + audit row for the same thread (system of record).
        with session_scope() as s:
            _seed_review(s, run_id=run_id, status="approved")

        # Sweep the terminated thread's checkpoint.
        swept = sweep_checkpoints(saver, thread_ids=[run_id])
        assert swept == 1
        assert saver.get_tuple(cfg) is None  # checkpoint gone

        # The audit rows survive — they are never swept.
        with session_scope() as s:
            assert s.query(HitlReview).filter_by(run_id=run_id).count() == 1
            assert s.query(AuditLog).filter_by(run_id=run_id).count() >= 1
            # cleanup
            s.query(AuditLog).filter_by(run_id=run_id).delete(synchronize_session=False)
            s.query(HitlReview).filter_by(run_id=run_id).delete(synchronize_session=False)


class TestReviewExpiry:
    def test_stale_pending_review_expires_without_deletion(self) -> None:
        run_id = "exp-" + uuid.uuid4().hex[:10]
        with session_scope() as s:
            review_id = _seed_review(
                s,
                run_id=run_id,
                status="pending",
                requested_at=datetime.now(UTC) - timedelta(days=60),
            )

        with session_scope() as s:
            expired = expire_stale_reviews(s, expiry_days=14)
            assert expired >= 1

        with session_scope() as s:
            row = s.get(HitlReview, review_id)
            # Transitioned, not deleted — the audit evidence is preserved.
            assert row is not None
            assert row.status == "expired"
            audit = s.query(AuditLog).filter_by(entity_id=review_id, event="hitl_expired").all()
            assert len(audit) == 1
            assert audit[0].actor == "system"
            # cleanup
            s.query(AuditLog).filter_by(run_id=run_id).delete(synchronize_session=False)
            s.query(HitlReview).filter_by(run_id=run_id).delete(synchronize_session=False)

    def test_fresh_pending_review_is_not_expired(self) -> None:
        run_id = "fresh-" + uuid.uuid4().hex[:10]
        with session_scope() as s:
            review_id = _seed_review(s, run_id=run_id, status="pending")
        with session_scope() as s:
            expire_stale_reviews(s, expiry_days=14)
        with session_scope() as s:
            assert s.get(HitlReview, review_id).status == "pending"
            s.query(HitlReview).filter_by(run_id=run_id).delete(synchronize_session=False)


class TestTerminatedThreadDetection:
    def test_decided_reviews_are_sweepable(self) -> None:
        run_id = "term-" + uuid.uuid4().hex[:10]
        with session_scope() as s:
            _seed_review(s, run_id=run_id, status="rejected", thread_id=run_id)
        with session_scope() as s:
            assert run_id in terminated_thread_ids(s)
            s.query(AuditLog).filter_by(run_id=run_id).delete(synchronize_session=False)
            s.query(HitlReview).filter_by(run_id=run_id).delete(synchronize_session=False)


# ------------------------------------------------------------------ helpers


def _seed_review(
    session: Any,
    *,
    run_id: str,
    status: str,
    thread_id: str | None = None,
    requested_at: datetime | None = None,
) -> str:
    """Write a HitlReview (+ its audit trail when decided) directly, bypassing the
    Document/Invoice/Run FK chain that a full run would create.

    `hitl_reviews` FKs run_id -> runs, so this needs a Run; but to keep the test
    focused on the checkpoint lifecycle we insert the review with a raw run_id
    that has no FK dependency by using the persistence helper against a seeded
    minimal Run is heavy — instead we rely on the review row alone, which the
    lifecycle functions key on. The FK is satisfied by a lightweight Run.
    """
    card = ReviewCard(
        invoice_id="inv",
        tenant_id="retail-demo",
        reason="over ceiling",
        proposed_route="route_for_approval",
        required_tier="controller",
        proposed_gl_account="7200",
        amount="5000.00",
        currency="USD",
    )
    _ensure_run(session, run_id=run_id)
    review_id = persist_pending_review(
        session,
        tenant_id="retail-demo",
        run_id=run_id,
        invoice_id="inv",
        card=card,
        channel="console",
        thread_id=thread_id or run_id,
        interrupt_id=None,
    )
    if requested_at is not None:
        review = session.get(HitlReview, review_id)
        review.requested_at = requested_at
    status_to_type = {
        "approved": ReviewDecisionType.APPROVE,
        "edited": ReviewDecisionType.EDIT,
        "rejected": ReviewDecisionType.REJECT,
    }
    if status in status_to_type:
        record_review_decision(
            session,
            review_id=review_id,
            tenant_id="retail-demo",
            run_id=run_id,
            invoice_id="inv",
            decision=ReviewDecision(
                decision=status_to_type[status],
                approver_identity="alice@controller",
            ),
        )
    return review_id


def _ensure_run(session: Any, *, run_id: str) -> None:
    """Create the minimal Document->Invoice->Run chain a HitlReview FK needs."""
    from ap_agent.persistence.models import Document, Run, Tenant
    from ap_agent.persistence.models import Invoice as InvoiceRow

    if session.get(Tenant, "retail-demo") is None:
        session.add(
            Tenant(
                tenant_id="retail-demo",
                name="retail-demo",
                industry="test",
                base_currency="USD",
                active_policy_version="1.0.0",
            )
        )
        session.flush()
    doc_id = uuid.uuid4().hex
    inv_id = uuid.uuid4().hex
    session.add(
        Document(
            document_id=doc_id,
            tenant_id="retail-demo",
            source="upload",
            filename="i.pdf",
            media_type="application/pdf",
            size_bytes=1,
            content_hash=uuid.uuid4().hex,
            storage_path=f"mem://{doc_id}",
        )
    )
    session.flush()
    session.add(
        InvoiceRow(
            invoice_id=inv_id,
            tenant_id="retail-demo",
            document_id=doc_id,
            source="upload",
            invoice_number="INV-DUR",
            invoice_date=date(2026, 2, 15),
            vendor_name_raw="Datamesh",
            currency="USD",
            subtotal=5000,
            total_amount=5000,
            header_confidence=0.99,
            overall_confidence=0.99,
        )
    )
    session.add(
        Run(
            run_id=run_id,
            tenant_id="retail-demo",
            invoice_id=inv_id,
            policy_version="1.0.0",
            status="running",
            started_at=datetime.now(UTC),
        )
    )
    session.flush()
