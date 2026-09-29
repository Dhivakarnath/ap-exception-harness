"""Slice 10 — HITL persistence: the review row and the audit trail (integration).

Marked `integration`: needs Postgres. The unit tests prove the pause/resume
decision logic in isolation; this proves the *system of record* — that an
escalation writes a pending `HitlReview` row, and the human's decision updates
that row and appends an `AuditLog` entry recording **who** decided and **when**
(the actor-and-timestamp trail FR-10.2/FR-11.3 require).

It drives the persistence helpers directly against a real session (rather than
the full graph) so the assertion is about the durable rows, not the interrupt
mechanics — which the unit suite already covers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from ap_agent.agent.hitl import (
    ReviewCard,
    ReviewDecision,
    ReviewDecisionType,
    persist_pending_review,
    record_review_decision,
)
from ap_agent.persistence.db import session_scope
from ap_agent.persistence.models import (
    AuditLog,
    Document,
    HitlReview,
    Invoice,
    Run,
    Tenant,
)

pytestmark = pytest.mark.integration

_TENANT = "retail-demo"


@pytest.fixture
def run_id() -> str:
    """A committed Run (with its Document+Invoice) the HitlReview/AuditLog can
    reference by FK, torn down after the test."""
    rid = str(uuid.uuid4())
    invoice_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    with session_scope() as s:
        if s.get(Tenant, _TENANT) is None:
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
        s.add(
            Document(
                document_id=document_id,
                tenant_id=_TENANT,
                source="upload",
                filename="invoice.pdf",
                media_type="application/pdf",
                size_bytes=1024,
                content_hash=uuid.uuid4().hex,
                storage_path=f"mem://{document_id}",
            )
        )
        s.flush()
        s.add(
            Invoice(
                invoice_id=invoice_id,
                tenant_id=_TENANT,
                document_id=document_id,
                source="upload",
                invoice_number="INV-HITL",
                invoice_date=date(2026, 2, 15),
                vendor_name_raw="Datamesh Analytics",
                currency="USD",
                subtotal=5000,
                total_amount=5000,
                header_confidence=0.99,
                overall_confidence=0.99,
            )
        )
        s.add(
            Run(
                run_id=rid,
                tenant_id=_TENANT,
                invoice_id=invoice_id,
                policy_version="1.0.0",
                status="running",
                started_at=datetime.now(UTC),
            )
        )
    yield rid
    # Tear down leaf-first, then the Document (whose FK cascade removes the
    # Invoice and its Run). Raw deletes avoid the ORM trying to orphan the
    # invoice's document_id before the cascade runs.
    with session_scope() as s:
        s.query(AuditLog).filter_by(run_id=rid).delete(synchronize_session=False)
        s.query(HitlReview).filter_by(run_id=rid).delete(synchronize_session=False)
        s.query(Run).filter_by(run_id=rid).delete(synchronize_session=False)
        s.query(Invoice).filter_by(invoice_id=invoice_id).delete(synchronize_session=False)
        s.query(Document).filter_by(document_id=document_id).delete(synchronize_session=False)


def _card() -> ReviewCard:
    return ReviewCard(
        invoice_id="inv",
        tenant_id=_TENANT,
        reason="Amount exceeds the touchless ceiling.",
        proposed_route="route_for_approval",
        required_tier="controller",
        proposed_gl_account="7200",
        amount="5000.00",
        currency="USD",
    )


class TestHitlPersistence:
    def test_pending_review_is_persisted_as_system_of_record(self, run_id: str) -> None:
        with session_scope() as s:
            review_id = persist_pending_review(
                s,
                tenant_id=_TENANT,
                run_id=run_id,
                invoice_id="inv",
                card=_card(),
                channel="console",
                thread_id=run_id,
                interrupt_id="int-1",
            )
        with session_scope() as s:
            row = s.get(HitlReview, review_id)
            assert row is not None
            assert row.status == "pending"
            assert row.required_tier == "controller"
            assert row.thread_id == run_id  # resumable: carries the LangGraph thread

    def test_decision_updates_review_and_writes_audit_with_actor_and_time(
        self, run_id: str
    ) -> None:
        with session_scope() as s:
            review_id = persist_pending_review(
                s,
                tenant_id=_TENANT,
                run_id=run_id,
                invoice_id="inv",
                card=_card(),
                channel="console",
                thread_id=run_id,
                interrupt_id=None,
            )

        decided_at = datetime.now(UTC)
        with session_scope() as s:
            record_review_decision(
                s,
                review_id=review_id,
                tenant_id=_TENANT,
                run_id=run_id,
                invoice_id="inv",
                decision=ReviewDecision(
                    decision=ReviewDecisionType.APPROVE,
                    approver_identity="alice@controller",
                    decided_at=decided_at,
                ),
            )

        with session_scope() as s:
            row = s.get(HitlReview, review_id)
            assert row.status == "approved"
            assert row.decided_by == "alice@controller"
            assert row.decided_at is not None

            audit = s.query(AuditLog).filter_by(run_id=run_id, entity_id=review_id).all()
            assert len(audit) == 1
            entry = audit[0]
            # The actor-and-timestamp trail: who decided, and when.
            assert entry.actor == "alice@controller"
            assert entry.event == "hitl_approved"
            assert entry.occurred_at is not None

    def test_reject_records_a_rejected_audit_event(self, run_id: str) -> None:
        with session_scope() as s:
            review_id = persist_pending_review(
                s,
                tenant_id=_TENANT,
                run_id=run_id,
                invoice_id="inv",
                card=_card(),
                channel="console",
                thread_id=run_id,
                interrupt_id=None,
            )
        with session_scope() as s:
            record_review_decision(
                s,
                review_id=review_id,
                tenant_id=_TENANT,
                run_id=run_id,
                invoice_id="inv",
                decision=ReviewDecision(
                    decision=ReviewDecisionType.REJECT,
                    approver_identity="bob@controller",
                    note="Duplicate.",
                ),
            )
        with session_scope() as s:
            row = s.get(HitlReview, review_id)
            assert row.status == "rejected"
            audit = s.query(AuditLog).filter_by(run_id=run_id, entity_id=review_id).one()
            assert audit.event == "hitl_rejected"
            assert audit.actor == "bob@controller"
