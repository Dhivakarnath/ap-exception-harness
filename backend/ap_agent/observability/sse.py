"""SSE/WebSocket bridge for RunEvent streaming (design §12.3).

The frontend reads a single normalized stream of `RunEvent`s per run. This
module provides:

* **Live streaming.** An in-flight run's `EventEmitter` is registered here; the
  SSE endpoint yields each event as it is emitted (a `queue.Queue` sink bridges
  the emitter's push to the endpoint's pull). When the run completes, the stream
  ends.

* **Replay.** For a completed run, the endpoint reconstructs `RunEvent`s from
  the persisted `check_results` / `decisions` rows, so the UI renders a finished
  run identically to a live one. A completed run's replay is a single batch
  (not streamed event by event), since the events are all available at once.

The SSE format is `text/event-stream`: each event is
``data: <JSON>\\n\\nid: <seq>\\n\\n``, so `seq` doubles as the `id` the browser
uses for reconnection.

This is a thin transport layer — the business logic of *which* events a run
produces lives in the `EventEmitter` and the supervisor's event-emission calls,
not here. Here, events go from their source to the wire.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from ap_agent.observability.events import EventEmitter, RunEvent

# Active emitters keyed by run_id. The supervisor registers its emitter here at
# run start and unregisters at completion; the SSE endpoint reads from it.
_active: dict[str, EventEmitter] = {}
_lock = threading.Lock()


def register_emitter(emitter: EventEmitter) -> None:
    with _lock:
        _active[emitter._run_id] = emitter


def unregister_emitter(run_id: str) -> None:
    with _lock:
        _active.pop(run_id, None)


def get_emitter(run_id: str) -> EventEmitter | None:
    with _lock:
        return _active.get(run_id)


def replay_events_from_db(session: Any, *, run_id: str) -> list[dict[str, Any]]:
    """Reconstruct RunEvent-shaped dicts from persisted rows for a completed run.

    Each `check_results` row becomes a `channel=check` event; the `decisions`
    row becomes a `channel=decision` event; and the run's cost columns become a
    `channel=cost` event. The same JSON shape the live emitter produces, so the
    frontend's reducer handles both paths with one code path.
    """
    from ap_agent.persistence.models import (
        CheckResultRow,
        DecisionRow,
        Invoice,
        Run,
    )

    events: list[dict[str, Any]] = []
    run = session.get(Run, run_id)
    if run is None:
        return events

    seq = 0
    trace_id = run.trace_id

    # Stage: the run itself
    events.append({
        "run_id": run_id,
        "trace_id": trace_id,
        "seq": seq,
        "channel": "stage",
        "at": run.started_at.isoformat() if run.started_at else None,
        "payload": {"name": "run", "status": run.status, "duration_ms": run.duration_ms},
    })
    seq += 1

    # Extraction: reconstructed from the persisted per-field provenance, so a
    # reloaded run shows the same fields, confidences, source regions, and the
    # Docling->Bedrock process metadata the live stream showed. Emitted right
    # after the stage event, matching the live order (extraction fires at the
    # end of the extract node, before the policy checks). Only present when the
    # run actually extracted from a document (field_provenance is null on the
    # state-first demo path).
    extraction = _replay_extraction(session, Invoice, run.invoice_id)
    if extraction is not None:
        events.append({
            "run_id": run_id,
            "trace_id": trace_id,
            "seq": seq,
            "channel": "extraction",
            "at": run.started_at.isoformat() if run.started_at else None,
            "payload": extraction,
        })
        seq += 1

    # Check results, in evaluation order.
    checks = (
        session.query(CheckResultRow)
        .filter_by(run_id=run_id)
        .order_by(CheckResultRow.sequence)
        .all()
    )
    for cr in checks:
        events.append({
            "run_id": run_id,
            "trace_id": trace_id,
            "seq": seq,
            "channel": "check",
            "at": cr.evaluated_at.isoformat() if cr.evaluated_at else None,
            "payload": {
                "name": cr.name,
                "category": cr.category,
                "verdict": cr.verdict,
                "severity": cr.severity,
                "reasoning": cr.reasoning,
                "threshold": cr.threshold,
                "actual": cr.actual,
                "inputs": cr.inputs or {},
                "forces_review": cr.forces_review,
                "citations": cr.citations or [],
                "duration_ms": cr.duration_ms,
            },
        })
        seq += 1

    # Decision.
    decision = session.query(DecisionRow).filter_by(run_id=run_id).first()
    if decision is not None:
        events.append({
            "run_id": run_id,
            "trace_id": trace_id,
            "seq": seq,
            "channel": "decision",
            "at": decision.decided_at.isoformat() if decision.decided_at else None,
            "payload": {
                "route": decision.route,
                "rationale": decision.rationale,
                "actor": decision.decided_by,
                "citations": decision.citations or [],
                "gl_account": decision.gl_account,
            },
        })
        seq += 1

    # Cost.
    events.append({
        "run_id": run_id,
        "trace_id": trace_id,
        "seq": seq,
        "channel": "cost",
        "at": (run.finished_at or run.started_at).isoformat() if run.finished_at else None,
        "payload": {
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "usd": float(run.cost_usd),
        },
    })
    return events


# The fields whose provenance the extraction event carries, in display order —
# the same set the live `_emit_extraction` surfaces, so replay matches it.
_EXTRACTION_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "invoice_date",
    "vendor_name",
    "currency",
    "subtotal",
    "total_amount",
    "po_reference",
)


def _replay_extraction(
    session: Any, invoice_model: Any, invoice_id: str
) -> dict[str, Any] | None:
    """Rebuild the extraction event payload from the persisted `Invoice` row.

    Reproduces the exact live shape — ``{"fields": [...], "meta": {...}}`` — from
    ``invoices.field_provenance`` (per-field confidence/region/bbox + the
    Docling->Bedrock process metadata). Returns None when there is no persisted
    provenance (the state-first demo path never extracted), so replay stays
    honest: no fabricated fields for a run that never read a document.
    """
    invoice = session.get(invoice_model, invoice_id)
    if invoice is None:
        return None
    provenance = getattr(invoice, "field_provenance", None)
    if not provenance:
        return None

    field_prov: dict[str, Any] = provenance.get("fields", {}) or {}
    fields: list[dict[str, Any]] = []
    for name in _EXTRACTION_FIELDS:
        fp = field_prov.get(name)
        if fp is None:
            continue
        fields.append({
            "name": name,
            "value": fp.get("value"),
            "confidence": fp.get("confidence"),
            "region_ref": fp.get("element_ref"),
            "page": fp.get("page"),
            "bbox": fp.get("bbox"),
        })

    payload: dict[str, Any] = {"fields": fields}
    line_items = provenance.get("line_items")
    if line_items:
        payload["line_items"] = line_items
    process = provenance.get("process")
    if process:
        payload["meta"] = process
    return payload


def sse_event(run_event: RunEvent | dict[str, Any]) -> str:
    """Format a RunEvent (or its dict shape) as an SSE text frame.

    ``id`` is set to ``seq`` so the browser can reconnect from the last seen seq.
    """
    wire = run_event.as_wire() if isinstance(run_event, RunEvent) else run_event
    return f"id: {wire.get('seq', 0)}\ndata: {json.dumps(wire, default=str)}\n\n"
