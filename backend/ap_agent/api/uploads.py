"""Upload-your-own-document runs — the real (non-scripted) end-to-end path.

Every other trigger in this API (`POST /runs`) starts from a *synthetic* demo
invoice built in code: no parsing, no model call, because the point of the demo
scenarios is to be reproducible offline. This module is the genuinely different
path a reviewer asked for: hand the system a real invoice file, and watch it go
through the actual pipeline — Docling parse, live Bedrock multimodal extraction,
the deterministic policy engine, the decision — streamed the same way.

It reuses the real ingress boundary (`IngressGateway`/`UploadAdapter`,
`ap_agent/ingest/`) rather than a bespoke upload path, so an uploaded file gets
the same validation, content-hash dedupe, and audit trail v2's other ingress
adapters (email, ERP webhook) will get. Parsing and rasterisation are blocking
CPU work (Docling, `pypdfium2`), so they run via `asyncio.to_thread` off the
event loop; the actual pipeline run (which makes a real, billed Bedrock call)
runs on its own background thread exactly like the scripted trigger, so this
handler returns as soon as the run is registered, not when it finishes.

Honest scope: this path always uses the live model (there is no "scripted
upload" — a real file has no scripted stand-in). A bad or unreadable invoice
fails loudly as an `ExtractionError`/`ParsingError`, per the no-coercion rule
(FR-2.4) the rest of the system holds to; it is not silently accepted.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from ap_agent.config import get_settings
from ap_agent.errors import APAgentError

router = APIRouter(tags=["uploads"])

# Runs started from an upload, keyed by run_id, so the document-image endpoint
# knows which stored file to re-render. Separate from the demo-scenario
# fixture map in `api.main` because the namespaces (fixture stems vs. content
# hashes) are unrelated.
_upload_documents: dict[str, str] = {}
_upload_documents_lock = threading.Lock()


def document_reference_for(run_id: str) -> str | None:
    """The storage reference for an upload run's document, if any.

    In-memory first (the process that handled the upload), then Postgres. The
    in-memory map is lost on a restart, but the file itself is content-addressed
    on disk and its reference is persisted as `Document.storage_path`, so a
    reloaded/cross-process run can still recover it — the run → invoice →
    document chain resolves the same reference. Without this fallback the source
    page 404s after a restart even though the bytes are still on disk.
    """
    with _upload_documents_lock:
        reference = _upload_documents.get(run_id)
    if reference is not None:
        return reference
    return _persisted_document_reference(run_id)


def _persisted_document_reference(run_id: str) -> str | None:
    """Recover an upload run's storage reference from the DB (survives restart)."""
    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import Document, Invoice, Run

    try:
        with session_scope() as session:
            run = session.get(Run, run_id)
            if run is None:
                return None
            invoice = session.get(Invoice, run.invoice_id)
            if invoice is None:
                return None
            document = session.get(Document, invoice.document_id)
            reference = getattr(document, "storage_path", None) if document else None
            return str(reference) if reference else None
    except Exception:
        return None


_MEDIA_TYPE_BY_EXTENSION: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".tiff": "image/tiff",
}


def _media_type_for_reference(reference: str) -> str:
    """Recover the media type a storage reference's extension encodes.

    A storage reference is `{sha256}{extension}` (`FilesystemDocumentStore.put`);
    the extension is exactly `ap_agent.ingest.storage.extension_for(media_type)`,
    so this is that mapping inverted rather than a guess.
    """
    _, _, ext = reference.rpartition(".")
    return _MEDIA_TYPE_BY_EXTENSION.get(f".{ext}", "application/pdf")


_UPLOAD_TENANTS = frozenset({"retail-demo", "manufacturing-demo"})


@router.post("/runs/upload")
async def upload_run(
    file: UploadFile = File(...),  # noqa: B008 - idiomatic FastAPI, not a real mutable-default bug
    mcp: bool = Form(default=False),
    tenant: str = Form(default="retail-demo"),
    allow_duplicate: bool = Form(default=False),
    deep_trace: bool | None = Form(default=None),
) -> JSONResponse:
    """Ingest a real invoice file and run it through the live pipeline.

    Always uses the real Bedrock extractor and coding model — there is no
    scripted stand-in for an arbitrary uploaded file, unlike the demo trigger's
    `live` toggle. `mcp` still selects whether the ERP call routes through the
    MCP server or in-process, matching the demo trigger's option.

    Returns the same `{run_id, stream_url}` shape as `POST /runs`, so the
    frontend's existing trigger-then-navigate flow needs no special case.
    """
    from ap_agent.ingest.adapters import IngressGateway, UploadAdapter
    from ap_agent.ingest.events import DocumentPayload
    from ap_agent.ingest.storage import FilesystemDocumentStore
    from ap_agent.persistence.db import session_scope

    content = await file.read()
    try:
        payload = DocumentPayload(
            filename=file.filename or "upload",
            media_type=file.content_type or "application/octet-stream",
            content=content,
        )
    except ValidationError as exc:
        return JSONResponse(
            {"error": "; ".join(e["msg"] for e in exc.errors())}, status_code=422
        )

    if tenant not in _UPLOAD_TENANTS:
        return JSONResponse(
            {
                "error": (
                    f"Unknown tenant '{tenant}'. "
                    f"Allowed: {', '.join(sorted(_UPLOAD_TENANTS))}."
                )
            },
            status_code=422,
        )
    tenant_id = tenant
    store = FilesystemDocumentStore(get_settings().upload_store_dir)
    try:
        with session_scope() as session:
            batch = IngressGateway(store).submit(
                session,
                UploadAdapter(tenant_id),
                [payload],
                allow_duplicate=allow_duplicate,
            )
    except Exception as exc:  # noqa: BLE001 - a DB-unreachable dev setup is reported, not fatal
        return JSONResponse(
            {"error": f"Could not record the upload: {exc}"}, status_code=503
        )

    if batch.rejected:
        rejection = batch.rejected[0]
        return JSONResponse(
            {
                "error": rejection.detail,
                "rejection": rejection.rejection.value if rejection.rejection else None,
                "existing_document_id": rejection.existing_document_id,
            },
            status_code=409,
        )

    accepted = batch.accepted[0]
    document_id = accepted.document_id
    if document_id is None:  # pragma: no cover - IngressResult.accept always sets this
        return JSONResponse(
            {"error": "Ingress accepted the document but returned no document_id."},
            status_code=500,
        )

    # `IngressGateway` already stored the bytes; `put` is idempotent (content-
    # addressed) so calling it again just returns the same reference without a
    # second write, giving us the reference for the image endpoint.
    reference = store.put(payload.content, media_type=payload.media_type)
    path = store.path_for(reference)
    if path is None:  # pragma: no cover - FilesystemDocumentStore always returns a path
        return JSONResponse(
            {"error": "Uploaded document has no readable storage path."},
            status_code=500,
        )

    # Parsing (Docling) and rasterisation (pypdfium2) are blocking CPU work;
    # keep them off the event loop. A parse failure here is reported cleanly —
    # no run is created for a document that never parsed.
    from ap_agent.extract.extractor import should_attach_images
    from ap_agent.ingest.parsing import parse_path, render_page_images

    try:
        parsed = await asyncio.to_thread(
            parse_path, path, media_type=payload.media_type, document_id=document_id
        )
    except APAgentError as exc:
        return JSONResponse(exc.as_dict(), status_code=422)

    page_images: list[tuple[str, bytes]] = []
    if should_attach_images(parsed):
        try:
            page_images = await asyncio.to_thread(
                render_page_images, path, media_type=payload.media_type
            )
        except APAgentError:
            # Multimodal grounding is an enhancement, not a requirement — a page
            # image failure should not block a document whose text layer parsed
            # fine. Extraction proceeds text-only.
            page_images = []

    from ap_agent.agent.supervisor import new_run_state
    from ap_agent.observability.events import EventEmitter
    from ap_agent.observability.sse import register_emitter

    from ap_agent.observability.status import deep_tracing_requested

    invoice_id = f"upload-{uuid.uuid4().hex[:8]}"
    state = new_run_state(
        tenant_id=tenant_id,
        invoice_id=invoice_id,
        document_id=document_id,
        invoice=None,
    )
    state.export_deep_traces = deep_tracing_requested(run_override=deep_trace)
    emitter = EventEmitter(run_id=state.run_id)
    register_emitter(emitter)

    with _upload_documents_lock:
        _upload_documents[state.run_id] = reference

    from ap_agent.api.main import (  # noqa: PLC0415 - avoids a main<->uploads import cycle
        _EMITTER_GRACE_SECONDS,
        _finalise_summary,
        _persist,
        _record_run,
        _run_upload_supervisor,
        _RunSummary,
        _supervisors,
        _supervisors_lock,
    )

    _record_run(
        _RunSummary(
            run_id=state.run_id,
            tenant_id=tenant_id,
            invoice_id=invoice_id,
            scenario="upload",
            vendor=payload.filename,
            total="—",
            status="running",
            started_at=datetime.now(UTC).isoformat(),
        )
    )

    def _execute() -> None:
        import time as _time

        from ap_agent.observability.sse import register_emitter, unregister_emitter

        out = None
        try:
            out = _run_upload_supervisor(
                state, emitter, parsed=parsed, page_images=page_images, mcp=mcp
            )
            register_emitter(emitter)
            _finalise_summary(state.run_id, out)
            _persist(out)
        finally:
            _time.sleep(_EMITTER_GRACE_SECONDS)
            unregister_emitter(state.run_id)
            if out is None or not getattr(out, "pending_hitl", False):
                with _supervisors_lock:
                    _supervisors.pop(state.run_id, None)

    threading.Thread(
        target=_execute, daemon=True, name=f"upload-{state.run_id}"
    ).start()

    return JSONResponse(
        {"run_id": state.run_id, "stream_url": f"/runs/{state.run_id}/events"}
    )


@router.get("/runs/{run_id}/document/image")
def upload_document_image(run_id: str) -> Response:
    """Render an upload run's actual uploaded page for the document viewer.

    Renders the *real* file the user submitted (unlike the demo scenarios,
    which stand in for a representative fixture) — this is the true
    highlight-back surface for a live-parsed upload.
    """
    from ap_agent.ingest.parsing import render_page_images
    from ap_agent.ingest.storage import FilesystemDocumentStore

    reference = document_reference_for(run_id)
    if reference is None:
        return JSONResponse(
            {"error": f"No uploaded document is recorded for run {run_id!r}."},
            status_code=404,
        )

    store = FilesystemDocumentStore(get_settings().upload_store_dir)
    path = store.path_for(reference)
    if path is None or not path.is_file():
        return JSONResponse(
            {"error": "The uploaded document is no longer available on disk."},
            status_code=404,
        )

    try:
        images = render_page_images(
            path, media_type=_media_type_for_reference(reference), max_pages=1
        )
    except APAgentError as exc:
        return JSONResponse(exc.as_dict(), status_code=500)
    if not images:
        return JSONResponse({"error": "No page rendered."}, status_code=500)
    _media, data = images[0]
    return Response(content=data, media_type="image/png")
