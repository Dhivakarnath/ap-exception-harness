"""Human-in-the-loop review: the pause, the record, and the resume (FR-11).

When the deterministic pipeline routes an invoice to a human, three things must
happen and be inseparable: the approver is *notified* in their own channel, the
request becomes the *auditable system of record* (a `HitlReview` row), and the
run *pauses durably* so it can resume exactly where it stopped when the human
decides. This module is the review card the interrupt carries and the decision
the human returns, plus the persistence helpers that make the queue the system
of record.

The interrupt/resume mechanics live in the supervisor's `hitl` node; this module
is the vocabulary. Two shapes:

* `ReviewCard` — what the interrupt surfaces to the caller/UI: why the invoice
  paused, the proposed decision, the DOA tier that must approve, and the fixed
  set of decisions the human may take. It carries no secret and no full account
  number (PII stays out of the review payload).
* `ReviewDecision` — what the human returns on resume: approve / edit / reject,
  the approver's identity (recorded as the actor of record), and, for an edit,
  the corrected GL coding. Segregation of duties is enforced downstream — the
  approver is a human, never the agent (FR-4.11).

The persistence helpers write the `HitlReview` row at escalation time (status
`pending`, carrying the LangGraph `thread_id`/`interrupt_id` so the paused run is
resumable) and update it on decision, and they append an `AuditLog` entry
recording *who* decided and *when* — the actor-and-timestamp trail the tests
assert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ReviewDecisionType(StrEnum):
    """What a human may do with an escalated invoice.

    Fixed set, not caller-supplied, so the agent cannot widen the affordances —
    there is deliberately no "auto_approve" a model could inject."""

    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ReviewCard:
    """The interrupt payload: what a reviewer needs to decide, and nothing more.

    Rendered by the UI (Slice 12) and returned on the run's ``__interrupt__``.
    Deliberately excludes anything sensitive — a review does not need a full bank
    account number to be actionable, and the PII redaction that guards tool
    output should not be undone by the escalation payload.
    """

    invoice_id: str
    tenant_id: str
    reason: str
    proposed_route: str
    required_tier: str | None
    proposed_gl_account: str | None
    amount: str
    currency: str
    citations: tuple[str, ...] = ()
    allowed_decisions: tuple[str, ...] = (
        ReviewDecisionType.APPROVE.value,
        ReviewDecisionType.EDIT.value,
        ReviewDecisionType.REJECT.value,
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "tenant_id": self.tenant_id,
            "reason": self.reason,
            "proposed_route": self.proposed_route,
            "required_tier": self.required_tier,
            "proposed_gl_account": self.proposed_gl_account,
            "amount": self.amount,
            "currency": self.currency,
            "citations": list(self.citations),
            "allowed_decisions": list(self.allowed_decisions),
        }


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """What the human returns on resume (the value of ``Command(resume=...)``).

    ``approver_identity`` is required for approve/edit — a decision with no named
    actor cannot be the approver of record (FR-4.11), and the supervisor refuses
    to finalise one. ``edited_gl_account`` applies only to an edit.
    """

    decision: ReviewDecisionType
    approver_identity: str | None = None
    note: str | None = None
    edited_gl_account: str | None = None
    edited_cost_center: str | None = None
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_resume(cls, payload: Any) -> ReviewDecision:
        """Parse the resume payload into a typed decision.

        Accepts a `ReviewDecision` directly (in-process tests) or a plain dict
        (an API/UI resume). Fails loud on an unrecognised decision type rather
        than defaulting — a garbled human decision must not silently become an
        approval.
        """
        if isinstance(payload, ReviewDecision):
            return payload
        if not isinstance(payload, dict):
            raise ValueError(f"HITL resume payload must be a decision, got {type(payload).__name__}")
        raw = payload.get("decision")
        try:
            decision = ReviewDecisionType(str(raw))
        except ValueError as exc:
            raise ValueError(
                f"Unknown HITL decision {raw!r}; expected one of "
                f"{[d.value for d in ReviewDecisionType]}."
            ) from exc
        return cls(
            decision=decision,
            approver_identity=payload.get("approver_identity"),
            note=payload.get("note"),
            edited_gl_account=payload.get("edited_gl_account"),
            edited_cost_center=payload.get("edited_cost_center"),
        )


# --------------------------------------------------------------- persistence


def persist_pending_review(
    session: Any,
    *,
    tenant_id: str,
    run_id: str,
    invoice_id: str,
    card: ReviewCard,
    channel: str,
    thread_id: str,
    interrupt_id: str | None,
) -> str:
    """Write the pending `HitlReview` row — the system of record (FR-11.3).

    Escalation reaches the reviewer in Slack/email, but *this row* is the
    auditable truth: it records why the invoice paused, which tier must approve,
    and the LangGraph ``thread_id``/``interrupt_id`` that make the paused run
    resumable. Returns the review id.
    """
    from ap_agent.persistence.models import HitlReview

    review = HitlReview(
        tenant_id=tenant_id,
        run_id=run_id,
        invoice_id=invoice_id,
        reason=card.reason,
        required_tier=card.required_tier,
        channel=channel,
        status="pending",
        thread_id=thread_id,
        interrupt_id=interrupt_id,
        requested_at=datetime.now(UTC),
    )
    session.add(review)
    session.flush()  # assign the id without ending the transaction
    return str(review.id)


def record_review_decision(
    session: Any,
    *,
    review_id: str,
    tenant_id: str,
    run_id: str,
    invoice_id: str,
    decision: ReviewDecision,
) -> None:
    """Update the `HitlReview` row and append an `AuditLog` entry (FR-10.2).

    The audit entry records the actor (the approver's identity) and the moment
    the decision was made — the actor-and-timestamp trail. Corrections are new
    entries, never edits, so the trail stays evidence.
    """
    from ap_agent.persistence.models import AuditLog, HitlReview

    status = {
        ReviewDecisionType.APPROVE: "approved",
        ReviewDecisionType.EDIT: "edited",
        ReviewDecisionType.REJECT: "rejected",
    }[decision.decision]

    review = session.get(HitlReview, review_id)
    if review is not None:
        review.status = status
        review.decided_at = decision.decided_at
        review.decided_by = decision.approver_identity
        review.decision_payload = {
            "decision": decision.decision.value,
            "note": decision.note,
            "edited_gl_account": decision.edited_gl_account,
            "edited_cost_center": decision.edited_cost_center,
        }

    session.add(
        AuditLog(
            tenant_id=tenant_id,
            run_id=run_id,
            entity_type="hitl_review",
            entity_id=review_id,
            event=f"hitl_{status}",
            actor=decision.approver_identity or "unknown",
            detail={
                "invoice_id": invoice_id,
                "decision": decision.decision.value,
                "note": decision.note,
                "decided_at": decision.decided_at.isoformat(),
            },
        )
    )
