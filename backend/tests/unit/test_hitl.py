"""Slice 10 — HITL escalation and resume (unit tier, no DB/model/network).

The load-bearing loop: an invoice that routes for approval **pauses** at the
interrupt, and only a human's decision — carried in on resume — completes it. The
tests drive the real LangGraph interrupt/resume machinery (an in-memory
checkpointer, a real `interrupt()` in the HITL node, a real `Command(resume=...)`)
against scripted models, so the pause/resume contract is exercised without
Bedrock or Postgres.

What they assert:

* approve / edit / reject each resume correctly, and the human is the approver of
  record (segregation of duties: `decided_by=HUMAN`, never the agent);
* an edit applies the human's corrected GL account;
* an approve/edit with no named approver fails loud (no anonymous approval);
* a $0 touchless ceiling forces every positive invoice to review;
* the toolset still contains no payment tool.

The audit-trail persistence (actor + timestamp in `HitlReview` + `AuditLog`) is
best-effort against a DB and is covered by an integration test; here the focus is
the pause/resume decision logic, which is pure.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ap_agent.agent.erp_client import InProcessErpClient
from ap_agent.agent.hitl import ReviewCard, ReviewDecision, ReviewDecisionType
from ap_agent.agent.supervisor import (
    Supervisor,
    SupervisorDeps,
    make_inmemory_checkpointer,
    new_run_state,
)
from ap_agent.core.canonical import Actor, Invoice, InvoiceLine, Route
from ap_agent.core.policy_pack import load_policy_pack
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money
from ap_agent.rag.coding import RawGLProposal, ScriptedCodingModel
from tests.unit.rag_fakes import make_chunk

_POLICY_PACKS = Path(__file__).resolve().parents[2] / "policy_packs"


@pytest.fixture
def pack() -> Any:
    return load_policy_pack(_POLICY_PACKS / "retail_non_po.yaml")


def _ex(value: Any, confidence: float = 0.99) -> Extracted[Any]:
    return Extracted(value=value, confidence=confidence, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="USD")


def _invoice(total: str = "5000.00") -> Invoice:
    return Invoice(
        invoice_id="inv-hitl",
        tenant_id="retail-demo",
        document_id="doc-1",
        invoice_number=_ex("INV-BIG"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex("Datamesh Analytics"),
        currency=_ex("USD"),
        subtotal=_ex(_money(total)),
        total_amount=_ex(_money(total)),
        po_reference=None,
        resolved_vendor_id="V-1001",
        lines=(
            InvoiceLine(
                line_number=1,
                description=_ex("Analytics platform annual subscription"),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(_money(total)),
                line_total=_ex(_money(total)),
            ),
        ),
    )


class _FakeRetriever:
    def retrieve(self, session: Any, **kwargs: Any) -> list[Any]:
        return [
            make_chunk(
                citation_ref="policy/gl_coding_policy_software_subscriptions.md#1",
                content="SaaS subscriptions code to 7200",
                lexical_rank=1,
                rrf_score=0.05,
            )
        ]


@contextmanager
def _fake_session() -> Any:
    yield None


def _supervisor(pack: Any) -> Supervisor:
    deps = SupervisorDeps(
        tenant_id="retail-demo",
        policy=pack,
        erp=InProcessErpClient(),
        retriever=_FakeRetriever(),
        coding_model=ScriptedCodingModel(
            RawGLProposal(
                gl_account="7200",
                confidence=0.95,
                citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",),
            )
        ),
        session_factory=_fake_session,
        checkpointer=make_inmemory_checkpointer(),
    )
    return Supervisor(deps)


def _run_to_pause(sup: Supervisor) -> Any:
    state = new_run_state(
        tenant_id="retail-demo", invoice_id="inv-hitl", document_id="doc-1", invoice=_invoice()
    )
    return sup.run(state)


# --------------------------------------------------------------- pause


class TestPause:
    def test_over_ceiling_invoice_pauses_for_review(self, pack: Any) -> None:
        sup = _supervisor(pack)
        paused = _run_to_pause(sup)
        assert paused.pending_hitl is True
        # No terminal human decision yet — it is awaiting one.
        assert paused.route is Route.ROUTE_FOR_APPROVAL

    def test_a_clean_small_invoice_does_not_pause(self, pack: Any) -> None:
        sup = _supervisor(pack)
        state = new_run_state(
            tenant_id="retail-demo", invoice_id="inv-small", document_id="doc-1", invoice=_invoice("500.00")
        )
        out = sup.run(state)
        assert out.pending_hitl is False
        assert out.route is Route.AUTO_APPROVE


# --------------------------------------------------------------- resume


class TestResumeDecisions:
    def test_approve_completes_with_human_as_approver(self, pack: Any) -> None:
        sup = _supervisor(pack)
        paused = _run_to_pause(sup)
        done = sup.resume(
            paused.run_id,
            ReviewDecision(decision=ReviewDecisionType.APPROVE, approver_identity="alice@controller"),
        )
        assert done.hitl_status == "approved"
        assert done.decision is not None
        assert done.decision.decided_by is Actor.HUMAN  # SoD: human, not agent
        assert done.decision.approver_identity == "alice@controller"

    def test_reject_holds_the_invoice(self, pack: Any) -> None:
        sup = _supervisor(pack)
        paused = _run_to_pause(sup)
        done = sup.resume(
            paused.run_id,
            ReviewDecision(
                decision=ReviewDecisionType.REJECT,
                approver_identity="bob@controller",
                note="Not a valid subscription.",
            ),
        )
        assert done.hitl_status == "rejected"
        assert done.decision.route is Route.REJECT
        assert "Not a valid subscription." in done.decision.rationale

    def test_edit_applies_the_corrected_gl_account(self, pack: Any) -> None:
        sup = _supervisor(pack)
        paused = _run_to_pause(sup)
        done = sup.resume(
            paused.run_id,
            ReviewDecision(
                decision=ReviewDecisionType.EDIT,
                approver_identity="carol@controller",
                edited_gl_account="6500",
            ),
        )
        assert done.hitl_status == "edited"
        assert done.decision.gl_coding is not None
        assert done.decision.gl_coding.gl_account == "6500"
        assert done.decision.decided_by is Actor.HUMAN

    def test_resume_via_plain_dict_payload(self, pack: Any) -> None:
        # An API/UI resume arrives as a dict, not a ReviewDecision instance.
        sup = _supervisor(pack)
        paused = _run_to_pause(sup)
        done = sup.resume(
            paused.run_id,
            {"decision": "approve", "approver_identity": "dave@controller"},
        )
        assert done.decision.approver_identity == "dave@controller"


# --------------------------------------------------------------- guards


class TestApprovalGuards:
    def test_approve_without_an_approver_fails_loud(self, pack: Any) -> None:
        sup = _supervisor(pack)
        paused = _run_to_pause(sup)
        # An approve with no named actor cannot be the approver of record.
        result = sup.resume(
            paused.run_id, ReviewDecision(decision=ReviewDecisionType.APPROVE, approver_identity=None)
        )
        assert result.error is not None
        assert result.error["error_type"] == "HumanInputRequiredError"

    def test_unknown_decision_type_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown HITL decision"):
            ReviewDecision.from_resume({"decision": "auto_approve"})


# --------------------------------------------------------------- $0 touchless


class TestTouchlessThreshold:
    def test_zero_ceiling_forces_every_positive_invoice_to_review(self, pack: Any) -> None:
        zero = pack.model_copy(
            update={"thresholds": pack.thresholds.model_copy(update={"touchless_max": Decimal("0")})}
        )
        sup = _supervisor(zero)
        state = new_run_state(
            tenant_id="retail-demo", invoice_id="inv-1", document_id="doc-1", invoice=_invoice("1.00")
        )
        out = sup.run(state)
        # A $1 invoice under a $0 touchless ceiling cannot be auto-approved.
        assert out.pending_hitl is True
        assert out.route is Route.ROUTE_FOR_APPROVAL


# --------------------------------------------------------------- review card


class TestReviewCard:
    def test_card_carries_no_full_account_number(self) -> None:
        # The review payload should not reintroduce PII the pipeline redacts.
        card = ReviewCard(
            invoice_id="i",
            tenant_id="retail-demo",
            reason="over ceiling",
            proposed_route="route_for_approval",
            required_tier="controller",
            proposed_gl_account="7200",
            amount="5000.00",
            currency="USD",
        )
        payload = card.as_dict()
        assert payload["allowed_decisions"] == ["approve", "edit", "reject"]
        assert "account_number" not in payload


# --------------------------------------------------------------- no payment


class TestNoPaymentPath:
    def test_toolset_has_no_payment_tool(self) -> None:
        from ap_agent.agent.tools import ALL_TOOL_NAMES

        assert ALL_TOOL_NAMES.isdisjoint({"pay", "disburse", "execute_payment", "remit"})
