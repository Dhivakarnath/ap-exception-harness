"""Persist a completed run to the audit tables (Run / CheckResultRow / DecisionRow).

The supervisor produces a `RunState` in memory; this is the seam that writes it
durably so a finished run can be listed and replayed from the database across
processes — closing the gap the UI's replay path depended on (INC-017).

The write is FK-ordered and idempotent-ish: it upserts the minimal parent rows a
`Run` requires (tenant → document → invoice) so a run can be persisted even for a
synthetic/demo invoice that was never ingested through the normal pipeline, then
writes the run, its per-check rows (in evaluation order), and its terminal
decision. It takes an already-open session (like `persist_pending_review`), so
the caller owns the transaction boundary.

Segregation-of-duties note: the `decisions` table has DB CheckConstraints that
mirror the domain model — agent-decided rows must not name an approver, and an
``auto_approve`` must not be human-decided. The mapping here respects that by
copying the canonical `Decision` fields verbatim (which the domain model already
validated).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ap_agent.agent.state import RunState
from ap_agent.llm.pricing import cost_usd


def persist_run(session: Any, state: RunState) -> str:
    """Write a completed, failed, or paused run and its results. Returns the run id.

    Inserts parents (tenant/document/invoice) if absent, then the run, its
    check-result rows, its decision row, and any error row. A run paused for
    human review is stored as ``awaiting_review`` with its extraction
    provenance, and a pending ``HitlReview`` row is written in the same
    transaction — the review cannot exist before the run row it references.
    Assumes an open session; the caller commits.
    """
    from ap_agent.persistence.models import (
        CheckResultRow,
        DecisionRow,
        Document,
        Invoice,
        Run,
        RunError,
        Tenant,
    )

    _ensure_tenant(session, Tenant, state.tenant_id)
    _ensure_document(session, Document, state)
    _ensure_invoice(session, Invoice, state)

    budget = state.budget
    input_tokens = getattr(budget, "input_tokens", 0) if budget else 0
    output_tokens = getattr(budget, "output_tokens", 0) if budget else 0
    started_at = getattr(budget, "started_at", None) or datetime.now(UTC)
    paused = bool(state.pending_hitl)
    # A paused escalation is not finished. Deleting the run row would cascade
    # away the HitlReview that points at it, so an existing row is updated and
    # only its child results are replaced.
    finished_at = None if paused else datetime.now(UTC)
    status = "awaiting_review" if paused else ("failed" if state.error else "completed")

    existing = session.get(Run, state.run_id)
    if existing is not None:
        session.query(CheckResultRow).filter_by(run_id=state.run_id).delete()
        session.query(DecisionRow).filter_by(run_id=state.run_id).delete()
        session.query(RunError).filter_by(run_id=state.run_id).delete()
        session.flush()
        existing.trace_id = state.trace_id
        existing.policy_version = state.policy_version or existing.policy_version
        existing.status = status
        existing.finished_at = finished_at
        existing.duration_ms = (
            (finished_at - started_at).total_seconds() * 1000.0
            if finished_at is not None and started_at is not None
            else existing.duration_ms
        )
        existing.input_tokens = input_tokens
        existing.output_tokens = output_tokens
        existing.cost_usd = Decimal(
            str(cost_usd(input_tokens=input_tokens, output_tokens=output_tokens))
        )
        existing.model_calls = getattr(budget, "model_calls", 0) if budget else 0
        existing.tool_calls = getattr(budget, "tool_calls", 0) if budget else 0
        existing.context_utilisation = state.context_utilisation
        session.flush()
    else:
        _insert_run(
            session,
            Run,
            state,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            budget=budget,
        )

    for sequence, result in enumerate(state.ledger.results):
        session.add(
            CheckResultRow(
                run_id=state.run_id,
                tenant_id=state.tenant_id,
                sequence=sequence,
                name=result.name,
                category=result.category.value,
                verdict=result.verdict.value,
                severity=result.severity.value,
                reasoning=result.reasoning,
                threshold=_truncate(result.threshold),
                actual=_truncate(result.actual),
                inputs=_jsonable(result.inputs),
                forces_review=result.forces_review,
                citations=list(result.citations),
                duration_ms=result.duration_ms,
                evaluated_at=result.evaluated_at,
            )
        )

    decision = state.decision
    if decision is not None:
        gl = decision.gl_coding
        session.add(
            DecisionRow(
                run_id=state.run_id,
                tenant_id=state.tenant_id,
                invoice_id=decision.invoice_id or state.invoice_id,
                route=decision.route.value,
                decided_by=decision.decided_by.value,
                rationale=decision.rationale,
                decided_at=decision.decided_at,
                gl_account=gl.gl_account if gl else None,
                cost_center=gl.cost_center if gl else None,
                entity=gl.entity if gl else None,
                project=gl.project if gl else None,
                gl_confidence=gl.confidence if gl else None,
                gl_inherited_from_po=gl.inherited_from_po if gl else False,
                required_approver_tier=decision.required_approver_tier,
                approver_identity=decision.approver_identity,
                citations=list(decision.citations),
            )
        )

    if state.error:
        err = state.error
        session.add(
            RunError(
                run_id=state.run_id,
                stage=str(err.get("stage") or "unknown"),
                error_class=str(err.get("error_class") or "unknown"),
                error_type=str(err.get("error_type") or "Error"),
                message=str(err.get("message") or ""),
                retryable=bool(err.get("retryable", False)),
                trace_id=state.trace_id,
                detail=_jsonable(err),
                raised_at=datetime.now(UTC),
            )
        )

    session.flush()
    if paused:
        _ensure_pending_review(session, state)
    return state.run_id


def _insert_run(
    session: Any,
    Run: Any,
    state: RunState,
    *,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
    input_tokens: int,
    output_tokens: int,
    budget: Any,
) -> None:
    session.add(
        Run(
            run_id=state.run_id,
            tenant_id=state.tenant_id,
            invoice_id=state.invoice_id,
            trace_id=state.trace_id,
            policy_version=state.policy_version or "unknown",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(
                (finished_at - started_at).total_seconds() * 1000.0
                if finished_at is not None and started_at is not None
                else None
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=Decimal(
                str(cost_usd(input_tokens=input_tokens, output_tokens=output_tokens))
            ),
            model_calls=getattr(budget, "model_calls", 0) if budget else 0,
            tool_calls=getattr(budget, "tool_calls", 0) if budget else 0,
            context_utilisation=state.context_utilisation,
        )
    )
    session.flush()


def _ensure_pending_review(session: Any, state: RunState) -> None:
    """Insert the pending review once the run row it references exists."""
    from ap_agent.persistence.models import HitlReview

    decision = state.decision
    if decision is None:
        return
    existing = (
        session.query(HitlReview)
        .filter(HitlReview.run_id == state.run_id, HitlReview.status == "pending")
        .one_or_none()
    )
    if existing is not None:
        state.hitl_review_id = str(existing.id)
        return
    review = HitlReview(
        tenant_id=state.tenant_id,
        run_id=state.run_id,
        invoice_id=state.invoice_id,
        reason=decision.rationale,
        required_tier=decision.required_approver_tier,
        channel="console",
        status="pending",
        thread_id=state.run_id,
        interrupt_id=None,
        requested_at=datetime.now(UTC),
    )
    session.add(review)
    session.flush()
    state.hitl_review_id = str(review.id)


# ------------------------------------------------------------- parent upserts


def _ensure_tenant(session: Any, Tenant: Any, tenant_id: str) -> None:
    if session.get(Tenant, tenant_id) is not None:
        return
    session.add(
        Tenant(
            tenant_id=tenant_id,
            name=tenant_id,
            industry="unknown",
            base_currency="USD",
            active_policy_version="unknown",
            is_active=True,
        )
    )
    session.flush()


def _ensure_document(session: Any, Document: Any, state: RunState) -> None:
    if session.get(Document, state.document_id) is not None:
        return
    session.add(
        Document(
            document_id=state.document_id,
            tenant_id=state.tenant_id,
            source="ui",
            filename=f"{state.document_id}.pdf",
            media_type="application/pdf",
            size_bytes=0,
            content_hash=state.document_id,
            storage_path=f"memory://{state.document_id}",
        )
    )
    session.flush()


def _ensure_invoice(session: Any, Invoice: Any, state: RunState) -> None:
    existing = session.get(Invoice, state.invoice_id)
    provenance = _field_provenance(state)
    if existing is not None:
        # Ingress may have stored the invoice before extraction finished. A
        # paused run still has to keep the reading that the review is about.
        if getattr(existing, "field_provenance", None) is None and provenance is not None:
            existing.field_provenance = provenance
            session.flush()
        return
    inv = state.invoice
    header_conf = _header_confidence(inv)
    session.add(
        Invoice(
            invoice_id=state.invoice_id,
            tenant_id=state.tenant_id,
            document_id=state.document_id,
            source="ui",
            invoice_number=str(inv.invoice_number.value) if inv else "UNKNOWN",
            invoice_date=inv.invoice_date.value if inv else datetime.now(UTC).date(),
            vendor_name_raw=str(inv.vendor_name.value) if inv else "UNKNOWN",
            currency=str(inv.currency.value) if inv else "USD",
            subtotal=inv.subtotal.value.amount if inv else Decimal("0"),
            total_amount=inv.total_amount.value.amount if inv else Decimal("0"),
            po_reference=(
                str(inv.po_reference.value)
                if inv and inv.po_reference is not None
                else None
            ),
            header_confidence=header_conf,
            overall_confidence=header_conf,
            field_provenance=provenance,
        )
    )
    session.flush()


# The header fields whose per-field provenance the UI surfaces. Same set the
# live extraction event emits, so replay from `field_provenance` reproduces it.
_PROVENANCE_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "invoice_date",
    "vendor_name",
    "currency",
    "subtotal",
    "total_amount",
    "po_reference",
)


def _field_provenance(state: RunState) -> dict[str, Any] | None:
    """Per-field confidence + source region + the extraction process metadata.

    Written to `invoices.field_provenance` so a completed run can show how each
    value was read — with what confidence, from which region of the page, by
    which parse strategy and model — without re-running extraction (which on a
    degraded scan could yield different values than the run under review). Built
    from the canonical `Invoice`'s `Extracted[T]` wrappers, so nothing is
    invented: absent regions/confidence are recorded as absent. Returns None on
    the state-first path (a pre-supplied invoice, e.g. the demo scenarios) where
    no real extraction happened and there is nothing honest to record.
    """
    inv = state.invoice
    if inv is None or state.extraction_meta is None:
        return None

    fields: dict[str, Any] = {}
    for name in _PROVENANCE_FIELDS:
        extracted = getattr(inv, name, None)
        if extracted is None:
            continue
        region = getattr(extracted, "region", None)
        bbox = getattr(region, "bbox", None)
        method = getattr(extracted, "method", None)
        fields[name] = {
            "value": None if extracted.value is None else str(extracted.value),
            "confidence": getattr(extracted, "confidence", None),
            "method": getattr(method, "value", None),
            "source_label": getattr(extracted, "source_label", None),
            "element_ref": getattr(region, "element_ref", None),
            "page": getattr(region, "page", None),
            "bbox": (
                {
                    "left": bbox.left,
                    "top": bbox.top,
                    "right": bbox.right,
                    "bottom": bbox.bottom,
                }
                if bbox is not None
                else None
            ),
        }

    line_items = [line.to_provenance() for line in inv.lines]

    jsonable: dict[str, Any] = _jsonable(
        {"process": state.extraction_meta, "fields": fields, "line_items": line_items}
    )
    return jsonable


def _header_confidence(inv: Any) -> float:
    """The minimum per-field confidence across the recovered header fields.

    Minimum, not average: a run is only as trustworthy as its least-certain
    field, and averaging would hide one shaky reading behind several confident
    ones. Falls back to 1.0 only when there are no confidences to read (the
    state-first demo path), never as a cosmetic default over real data.
    """
    if inv is None:
        return 1.0
    confidences = [
        c
        for name in _PROVENANCE_FIELDS
        if (extracted := getattr(inv, name, None)) is not None
        and (c := getattr(extracted, "confidence", None)) is not None
    ]
    return float(min(confidences)) if confidences else 1.0


# ------------------------------------------------------------------- helpers


def _truncate(value: Any, limit: int = 128) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _jsonable(value: Any) -> Any:
    """Coerce a dict of arbitrary values into JSON-serialisable form for JSONB."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool):
        return value
    return str(value)
