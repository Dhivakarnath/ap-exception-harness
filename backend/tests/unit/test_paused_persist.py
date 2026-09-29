"""A paused HITL run is stored with its extraction, not dropped until resume."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from ap_agent.agent.supervisor import new_run_state
from ap_agent.core.canonical import Actor, Decision, Route
from ap_agent.persistence.models import HitlReview, Invoice, Run
from ap_agent.persistence.run_writer import persist_run
from tests.unit.test_hitl import _invoice


def _state(*, pending: bool):
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="inv-hitl",
        document_id="doc-1",
        invoice=_invoice("3929.48"),
    )
    state.run_id = "upload-paused"
    state.pending_hitl = pending
    state.extraction_meta = {"strategy": "docling", "model": "test"}
    state.decision = Decision(
        invoice_id="inv-hitl",
        route=Route.ROUTE_FOR_APPROVAL,
        decided_by=Actor.AGENT,
        rationale="Over the retail touchless ceiling.",
        decided_at=datetime(2026, 9, 25, tzinfo=UTC),
        required_approver_tier="controller",
    )
    return state


def _session(*, run=None, invoice=None, pending_review=None) -> MagicMock:
    session = MagicMock()

    def _get(model, _key):
        name = getattr(model, "__name__", "")
        if name == "Run":
            return run
        if name == "Invoice":
            return invoice
        return object()

    session.get.side_effect = _get
    session.query.return_value.filter.return_value.one_or_none.return_value = pending_review

    def _flush() -> None:
        for call in session.add.call_args_list:
            obj = call.args[0]
            if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                obj.id = "rev-new"

    session.flush.side_effect = _flush
    return session


def _added(session: MagicMock, model: type) -> list:
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], model)]


def test_paused_run_stores_extraction_and_one_pending_review() -> None:
    session = _session()
    state = _state(pending=True)

    persist_run(session, state)

    runs = _added(session, Run)
    invoices = _added(session, Invoice)
    reviews = _added(session, HitlReview)
    assert len(runs) == 1
    assert runs[0].status == "awaiting_review"
    assert runs[0].finished_at is None
    assert invoices[0].field_provenance["fields"]["vendor_name"]["value"] == "Datamesh Analytics"
    assert invoices[0].field_provenance["process"]["strategy"] == "docling"
    assert len(reviews) == 1
    assert reviews[0].status == "pending"
    assert reviews[0].run_id == "upload-paused"
    assert state.hitl_review_id == str(reviews[0].id)
    session.delete.assert_not_called()


def test_existing_paused_run_is_updated_without_a_second_review() -> None:
    existing = SimpleNamespace(
        policy_version="v1",
        duration_ms=None,
        status="running",
        finished_at=None,
    )
    invoice = SimpleNamespace(field_provenance=None)
    review = SimpleNamespace(id="rev-existing")
    session = _session(run=existing, invoice=invoice, pending_review=review)
    state = _state(pending=True)

    persist_run(session, state)

    assert existing.status == "awaiting_review"
    assert existing.finished_at is None
    assert invoice.field_provenance["fields"]["total_amount"]["value"].startswith("3929")
    assert _added(session, Run) == []
    assert _added(session, HitlReview) == []
    assert state.hitl_review_id == "rev-existing"
    session.delete.assert_not_called()


def test_resolved_run_is_finished_and_does_not_open_a_review() -> None:
    session = _session()
    state = _state(pending=False)

    persist_run(session, state)

    runs = _added(session, Run)
    assert runs[0].status == "completed"
    assert runs[0].finished_at is not None
    assert _added(session, HitlReview) == []
