"""FastAPI application — the transparency UI's backend (design §12.2, §12.3).

This wires the observability stream and KPI endpoints (`api/streams.py`) into a
real app, adds a CORS allowlist for the browser UI, a health check, and a
**run-trigger** endpoint so the UI can start an invoice run and watch it stream.

The trigger runs a real supervisor over a demo invoice with an `EventEmitter`
attached and registered in the SSE bridge, so the UI's `GET /runs/{id}/events`
streams the run live — the same normalized `RunEvent` envelope the reducer folds
into `RunState`. By default it uses **scripted** models (offline, no AWS) so the
UI works on a clean clone; `live=true` switches to real Bedrock + Titan (and
`mcp=true` routes the ERP over the MCP server), the same escalation the demos
use.

A small in-memory registry tracks the runs this process has handled so the
InvoiceQueue has something to list. Persisted replay-from-DB (for runs that
outlive the process) depends on a run-persistence writer that is not built yet;
that is stated honestly rather than faked — the live stream is what this endpoint
guarantees.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ap_agent.api.lifecycle import lifespan
from ap_agent.api.streams import router as streams_router
from ap_agent.config import get_settings

app = FastAPI(
    title="AP Exception Agent",
    version="1.0.0",
    summary="Agentic accounts-payable exception handling — transparency API.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(streams_router)

from ap_agent.api.hitl import router as hitl_router  # noqa: E402
from ap_agent.api.platform import router as platform_router  # noqa: E402
from ap_agent.api.uploads import router as uploads_router  # noqa: E402

app.include_router(platform_router)
app.include_router(uploads_router)
app.include_router(hitl_router)

# How long a finished run's emitter stays registered so a late-connecting client
# still catches its (buffered) event stream. See `_execute`.
_EMITTER_GRACE_SECONDS = 20.0

# In-process Supervisor cache for HITL resume (fast path). When
# CHECKPOINTER_BACKEND=postgres, resume also works cross-process by rebuilding
# a fresh Supervisor from the durable checkpoint (see resume_run).
_supervisors: dict[str, Any] = {}
_supervisors_lock = threading.Lock()


# ------------------------------------------------------------------ health
@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Cheap, dependency-free."""
    return {"status": "ok"}


# --------------------------------------------------- in-memory run registry
class _RunSummary(BaseModel):
    """A lightweight record of a run this process handled, for the queue view."""

    run_id: str
    tenant_id: str
    invoice_id: str
    scenario: str
    vendor: str
    total: str
    status: str
    route: str | None = None
    gl_account: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    started_at: str
    finished_at: str | None = None
    pending_review: bool = False
    # The fixture stem whose rendered page best represents this run's document,
    # so the run-detail viewer can show a real source page (not a mock). None
    # when the scenario has no representative fixture.
    document_stem: str | None = None
    trace_id: str | None = None
    trace_url: str | None = None
    duration_ms: float | None = None


# The demo trigger scenarios are synthetic invoices, not fixture files. This
# maps each to the generated fixture whose rendered page faithfully stands in
# for it (same shape of document), so the document viewer shows a genuine page.
_SCENARIO_FIXTURE: dict[str, str] = {
    "auto_approve": "clean_touchless",
    "route_for_approval": "clean_over_threshold",
}


def _fixture_stem_for(scenario: str) -> str | None:
    return _SCENARIO_FIXTURE.get(scenario)


_runs: dict[str, _RunSummary] = {}
_runs_lock = threading.Lock()


def _record_run(summary: _RunSummary) -> None:
    with _runs_lock:
        _runs[summary.run_id] = summary


@app.get("/runs")
def list_runs(tenant: str = "all") -> list[_RunSummary]:
    """The run history, newest first.

    Reads persisted runs from Postgres so history survives a reload or a backend
    restart (the KPI cards already read the DB; the list used to read only this
    process's in-memory registry, so a reload showed "No runs yet" while the KPIs
    counted the real rows — INC-020). The in-memory registry is merged on top so
    a still-running run this process is streaming shows immediately, before it is
    persisted at completion; the live entry wins on a per-`run_id` basis.

    ``tenant`` filters to one demo tenant or ``all`` for the combined queue.
    """
    from ap_agent.api.hitl import pending_review_run_ids
    from ap_agent.tenants import ALL_TENANTS, DEMO_TENANT_IDS

    if tenant == ALL_TENANTS:
        persisted: list[_RunSummary] = []
        for demo_tenant in DEMO_TENANT_IDS:
            persisted.extend(_persisted_runs(tenant_id=demo_tenant))
    else:
        persisted = _persisted_runs(tenant_id=tenant)
    pending_ids = pending_review_run_ids()
    with _runs_lock:
        live = dict(_runs)
    if tenant != ALL_TENANTS:
        live = {k: v for k, v in live.items() if v.tenant_id == tenant}
    merged: dict[str, _RunSummary] = {r.run_id: r for r in persisted}
    merged.update(live)  # a live/in-flight run supersedes its persisted snapshot
    for run_id in pending_ids:
        if run_id in merged:
            merged[run_id] = merged[run_id].model_copy(
                update={"status": "awaiting_review", "pending_review": True}
            )
    return sorted(merged.values(), key=lambda r: r.started_at, reverse=True)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> JSONResponse:
    """A single run's summary (for the run-detail page's header/metadata).

    In-memory first (a live run this process is streaming), then Postgres, so a
    reloaded or cross-process run-detail page still resolves its header.
    """
    with _runs_lock:
        summary = _runs.get(run_id)
    if summary is None:
        summary = _persisted_run(run_id)
    if summary is None:
        return JSONResponse({"error": "run not found"}, status_code=404)
    return JSONResponse(summary.model_dump())


def _persisted_runs(*, tenant_id: str = "retail-demo") -> list[_RunSummary]:
    """Real (live) runs for a tenant, mapped to the queue summary shape.

    Only runs from an actual document read are shown: a genuine Bedrock call
    (`input_tokens > 0`) or persisted extraction provenance. Scripted/demo and
    seeded runs — which never call the model and carry no provenance — are
    excluded so the history reflects invoices actually processed, not fixtures
    used to populate KPI aggregates.
    """
    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.document_runs import document_processed_runs_query
    from ap_agent.persistence.models import Run

    try:
        with session_scope() as session:
            rows = (
                document_processed_runs_query(session, tenant_id=tenant_id)
                .order_by(Run.started_at.desc())
                .all()
            )
            return [_summary_from_run(run) for run in rows]
    except Exception:
        # History is best-effort: a DB hiccup must not blank the queue. The live
        # in-memory registry still answers for this process's own runs.
        return []


def _persisted_run(run_id: str) -> _RunSummary | None:
    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import Run

    try:
        with session_scope() as session:
            run = session.get(Run, run_id)
            return _summary_from_run(run) if run is not None else None
    except Exception:
        return None


def _summary_from_run(run: Any) -> _RunSummary:
    """Map a persisted `Run` (with its invoice + decision) to `_RunSummary`.

    Reads inside the caller's session, so the lazy `invoice`/`decision`
    relationships resolve before the session closes.
    """
    invoice = run.invoice
    decision = run.decision
    vendor = getattr(invoice, "vendor_name_raw", None) or "—"
    total = (
        str(getattr(invoice, "total_amount", "")) if invoice is not None else ""
    ) or "—"
    route = getattr(decision, "route", None)
    pending_review = route == "route_for_approval" and (
        getattr(decision, "decided_by", None) != "human"
    )
    status = "awaiting_review" if pending_review else run.status
    tid = run.trace_id
    from ap_agent.observability.langfuse_export import trace_url as langfuse_trace_url

    return _RunSummary(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        invoice_id=run.invoice_id,
        scenario="upload",
        vendor=str(vendor),
        total=str(total),
        status=status,
        route=route,
        gl_account=getattr(decision, "gl_account", None),
        input_tokens=int(run.input_tokens or 0),
        output_tokens=int(run.output_tokens or 0),
        usd=float(run.cost_usd or 0.0),
        started_at=(run.started_at.isoformat() if run.started_at else ""),
        finished_at=(run.finished_at.isoformat() if run.finished_at else None),
        pending_review=pending_review,
        document_stem=None,
        trace_id=tid,
        trace_url=langfuse_trace_url(tid) if tid else None,
        duration_ms=float(run.duration_ms) if run.duration_ms is not None else None,
    )


# --------------------------------------------------------- run trigger
class TriggerRequest(BaseModel):
    """What invoice to run and how (offline scripted by default)."""

    scenario: str = "auto_approve"
    live: bool = False
    mcp: bool = False
    deep_trace: bool | None = None
    """When set, overrides the global deep-trace preference for this run."""


class TriggerResponse(BaseModel):
    run_id: str
    stream_url: str


@app.post("/runs")
def trigger_run(req: TriggerRequest) -> TriggerResponse:
    """Start an invoice run and return its id + stream URL.

    The run executes on a background thread with an `EventEmitter` registered in
    the SSE bridge, so the client can immediately open `GET /runs/{id}/events`
    and watch it stream. Returns as soon as the run is registered — not when it
    finishes — so the UI sees the stream from the first event.
    """
    import uuid

    from ap_agent.agent.supervisor import new_run_state
    from ap_agent.observability.events import EventEmitter

    # Unique ids per run so persisted invoice/document rows do not collide when
    # the same scenario is run repeatedly.
    suffix = uuid.uuid4().hex[:8]
    invoice = _demo_invoice(req.scenario, suffix=suffix)
    from ap_agent.observability.status import deep_tracing_requested

    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id=invoice.invoice_id,
        document_id=f"ui-doc-{suffix}",
        invoice=invoice,
    )
    state.export_deep_traces = deep_tracing_requested(run_override=req.deep_trace)
    emitter = EventEmitter(run_id=state.run_id)

    # Register in the SSE bridge before the run starts, so a client that opens
    # the stream immediately sees every event including the first.
    from ap_agent.observability.sse import register_emitter

    register_emitter(emitter)

    _record_run(
        _RunSummary(
            run_id=state.run_id,
            tenant_id="retail-demo",
            invoice_id=invoice.invoice_id,
            scenario=req.scenario,
            vendor=str(invoice.vendor_name.value),
            total=str(invoice.total_amount.value),
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            document_stem=_fixture_stem_for(req.scenario),
        )
    )

    def _execute() -> None:
        import time as _time

        from ap_agent.observability.sse import register_emitter, unregister_emitter

        out = None
        try:
            out = _run_supervisor(state, emitter, req)
            # RE-register the emitter IMMEDIATELY after the run returns, before
            # any slower work (persistence). The supervisor unregisters the
            # emitter in its own `finally` the instant the run completes, so a
            # sub-second run would otherwise finish before the browser's
            # EventSource connects. The emitter still holds all its buffered
            # events, so a client connecting just after triggering gets the full
            # replay; persistence below makes replay survive beyond the window.
            register_emitter(emitter)
            _finalise_summary(state.run_id, out)
            _persist(out)
        finally:
            _time.sleep(_EMITTER_GRACE_SECONDS)
            unregister_emitter(state.run_id)
            # A paused run keeps its Supervisor for resume; a terminal run drops
            # it so the process does not accumulate instances.
            if out is None or not getattr(out, "pending_hitl", False):
                with _supervisors_lock:
                    _supervisors.pop(state.run_id, None)

    threading.Thread(target=_execute, daemon=True, name=f"run-{state.run_id}").start()

    return TriggerResponse(
        run_id=state.run_id, stream_url=f"/runs/{state.run_id}/events"
    )


class ReviewRequest(BaseModel):
    """A human's HITL decision on a paused run."""

    decision: str  # approve | edit | reject
    approver_identity: str | None = None
    note: str | None = None
    edited_gl_account: str | None = None
    edited_cost_center: str | None = None


@app.post("/runs/{run_id}/review")
def resume_run(run_id: str, req: ReviewRequest) -> JSONResponse:
    """Resume a paused (awaiting-review) run with the human's decision.

    Fast path: the same in-process Supervisor that paused the run (when still
    held in `_supervisors`). Durable path: rebuild a fresh Supervisor from the
    Postgres checkpoint using the pending `HitlReview` row — survives API
    restarts and multi-instance deployments when CHECKPOINTER_BACKEND=postgres.
    """
    decision = {
        "decision": req.decision,
        "approver_identity": req.approver_identity,
        "note": req.note,
        "edited_gl_account": req.edited_gl_account,
        "edited_cost_center": req.edited_cost_center,
    }

    with _supervisors_lock:
        supervisor = _supervisors.get(run_id)

    if supervisor is None:
        supervisor, err = _supervisor_for_durable_resume(run_id)
        if err is not None:
            return JSONResponse({"error": err}, status_code=409)
        if supervisor is None:
            return JSONResponse(
                {"error": "Unable to restore the paused run."},
                status_code=500,
            )

    try:
        out = supervisor.resume(run_id, decision)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as an error
        return JSONResponse({"error": str(exc)}, status_code=400)

    _finalise_summary(run_id, out)
    _persist(out)
    with _supervisors_lock:
        _supervisors.pop(run_id, None)
    route = out.decision.route.value if getattr(out, "decision", None) else None
    return JSONResponse({"run_id": run_id, "route": route})


def _supervisor_for_durable_resume(run_id: str) -> tuple[Any | None, str | None]:
    """Build a fresh Supervisor for cross-process HITL resume, or return an error."""
    from ap_agent.api.supervisor_factory import build_resume_supervisor
    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import HitlReview

    try:
        with session_scope() as session:
            review = (
                session.query(HitlReview)
                .filter(HitlReview.run_id == run_id, HitlReview.status == "pending")
                .one_or_none()
            )
            if review is None:
                return None, "run is not awaiting review (no pending HitlReview row)"
            tenant_id = str(review.tenant_id)
        return build_resume_supervisor(tenant_id=tenant_id), None
    except RuntimeError as exc:
        return None, str(exc)
    except Exception:
        return None, "run is not awaiting review (database unavailable)"


def _persist(out: Any) -> None:
    """Persist a completed, failed, or paused run to the audit tables.

    Best-effort because the offline demo can run without a database. When a DB
    is present the run, its invoice, and its extraction provenance are written
    even while the run is paused for review, so a restart still lists the
    invoice and the review queue. Live evaluation waits until the run is
    terminal — a paused decision is not a finished one.
    """
    import contextlib
    import logging

    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.run_writer import persist_run

    persisted = False
    try:
        with session_scope() as session:
            persist_run(session, out)
            persisted = True
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to persist run %s", getattr(out, "run_id", None)
        )

    if persisted and not getattr(out, "pending_hitl", False):
        from ap_agent.evaluation.live import schedule_live_evaluation

        # Scheduling is deliberately best-effort at the API boundary. The
        # evaluator persists its own pending/running/failed state; a judge
        # outage must never roll back the AP decision that was already made.
        with contextlib.suppress(Exception):
            schedule_live_evaluation(out)


def _finalise_summary(run_id: str, out: Any) -> None:
    with _runs_lock:
        summary = _runs.get(run_id)
        if summary is None:
            return
        decision = getattr(out, "decision", None)
        budget = getattr(out, "budget", None)
        from ap_agent.llm.pricing import cost_usd

        in_tok = getattr(budget, "input_tokens", 0) if budget else 0
        out_tok = getattr(budget, "output_tokens", 0) if budget else 0
        pending = bool(getattr(out, "pending_hitl", False))
        if pending:
            status = "awaiting_review"
        elif getattr(out, "error", None):
            status = "failed"
        else:
            status = "completed"
        update: dict[str, Any] = {
            "status": status,
            "route": decision.route.value if decision is not None else None,
            "gl_account": (
                out.gl_coding.gl_account
                if getattr(out, "gl_coding", None) is not None
                else None
            ),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "usd": cost_usd(input_tokens=in_tok, output_tokens=out_tok),
            "finished_at": None if pending else datetime.now(UTC).isoformat(),
            "pending_review": pending,
        }
        # The upload path starts with placeholder vendor/total (the real values
        # are not known until extraction reads them). Backfill from the
        # extracted invoice once it exists so the queue/header show the real
        # document, not the placeholder.
        invoice = getattr(out, "invoice", None)
        if invoice is not None:
            update["vendor"] = str(invoice.vendor_name.value)
            update["total"] = str(invoice.total_amount.value)
        updated = summary.model_copy(update=update)
        _runs[run_id] = updated


# --------------------------------------------------------- run assembly
def _run_supervisor(state: Any, emitter: Any, req: TriggerRequest) -> Any:
    """Build supervisor (scripted by default, live on request) and run."""
    from ap_agent.api.supervisor_factory import build_supervisor

    supervisor = build_supervisor(
        tenant_id="retail-demo",
        live=req.live,
        mcp=req.mcp,
        emitter=emitter,
    )
    with _supervisors_lock:
        _supervisors[state.run_id] = supervisor
    return supervisor.run(state)


def _run_upload_supervisor(
    state: Any,
    emitter: Any,
    *,
    parsed: Any,
    page_images: list[tuple[str, bytes]],
    mcp: bool,
) -> Any:
    """Run the supervisor over a real uploaded document (extract, not replay).

    Unlike `_run_supervisor`, `state.invoice` is always `None` here: the extract
    node must actually call the model, so `deps.extractor`/`parsed_document` are
    populated rather than the demo path's pre-built invoice. Always live — a
    real uploaded file has no scripted stand-in — so this always calls Bedrock.
    """
    from ap_agent.api.supervisor_factory import build_upload_supervisor

    supervisor = build_upload_supervisor(
        tenant_id="retail-demo",
        mcp=mcp,
        emitter=emitter,
        parsed=parsed,
        page_images=page_images,
    )
    with _supervisors_lock:
        _supervisors[state.run_id] = supervisor
    return supervisor.run(state)


# --------------------------------------------------------- demo invoices
def _demo_invoice(scenario: str, *, suffix: str = "demo") -> Any:
    """A small set of demo invoices covering the routes the UI should show.

    `suffix` makes the invoice/document ids unique per run so the persistence
    writer does not collide on the invoice primary key across repeated runs.
    """
    from ap_agent.core.canonical import Invoice, InvoiceLine
    from ap_agent.core.primitives import Extracted, ExtractionMethod, Money

    def ex(value: Any) -> Any:
        return Extracted(
            value=value, confidence=0.99, method=ExtractionMethod.PARSED_STRUCTURE
        )

    def money(amount: str) -> Money:
        return Money(amount=Decimal(amount), currency="USD")

    # amount decides the route: under the touchless ceiling -> auto-approve;
    # above it -> route for approval (the HITL path).
    totals = {
        "auto_approve": "500.00",
        "route_for_approval": "5000.00",
    }
    total = totals.get(scenario, "500.00")
    return Invoice(
        invoice_id=f"ui-inv-{scenario}-{suffix}",
        tenant_id="retail-demo",
        document_id=f"ui-doc-{suffix}",
        invoice_number=ex(f"INV-{suffix.upper()}"),
        invoice_date=ex(date(2026, 2, 15)),
        vendor_name=ex("Datamesh Analytics"),
        currency=ex("USD"),
        subtotal=ex(money(total)),
        total_amount=ex(money(total)),
        po_reference=None,
        resolved_vendor_id="V-1001",
        lines=(
            InvoiceLine(
                line_number=1,
                description=ex("Analytics platform annual subscription"),
                quantity=ex(Decimal("1")),
                unit_price=ex(money(total)),
                line_total=ex(money(total)),
            ),
        ),
    )
