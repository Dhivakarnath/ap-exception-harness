"""Ingestion adapters.

An adapter's only job is to turn something source-specific into an
`InvoiceReceived` event (FR-1.2). Everything shared — validation, content-hash
dedupe, persistence of the `documents` row, audit logging — lives in
`IngressGateway` so it cannot be forgotten by a new adapter (FR-1.3).

v1 ships `UploadAdapter`. The v2 adapters (email, ERP webhook, bucket watcher) are
sketched as protocol-conforming stubs so the seam is real and testable rather than
aspirational: `test_ingest.py` asserts they satisfy the protocol and that the
gateway needs no change to accept them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ap_agent.core.canonical import InvoiceSource
from ap_agent.ingest.events import (
    BatchIngressResult,
    DocumentPayload,
    IngressRejection,
    IngressResult,
    InvoiceReceived,
)
from ap_agent.ingest.storage import DocumentStore
from ap_agent.persistence.models import AuditLog, Document


@runtime_checkable
class IngestionAdapter(Protocol):
    """Converts a source-specific input into normalised events.

    Adapters do not validate, deduplicate, or persist — the gateway does. An
    adapter that took on those jobs would be duplicating logic that must behave
    identically across every ingress path.
    """

    @property
    def source(self) -> InvoiceSource:
        """Which ingress path this adapter represents."""
        ...

    def to_events(self, raw: object) -> Iterable[InvoiceReceived]:
        """Yield one event per document found in the input."""
        ...


class UploadAdapter:
    """Manual upload — single or batch (FR-1.1).

    The minor path in production but the one a reviewer will actually use, and the
    one that makes the demo possible.
    """

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id

    @property
    def source(self) -> InvoiceSource:
        return InvoiceSource.UPLOAD

    def to_events(self, raw: object) -> Iterable[InvoiceReceived]:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise TypeError(
                "UploadAdapter expects a sequence of DocumentPayload objects, "
                f"got {type(raw).__name__}."
            )

        batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        for position, payload in enumerate(raw, start=1):
            if not isinstance(payload, DocumentPayload):
                raise TypeError(
                    f"UploadAdapter item {position} is {type(payload).__name__}, "
                    "expected DocumentPayload."
                )
            yield InvoiceReceived(
                tenant_id=self._tenant_id,
                payload=payload,
                source=InvoiceSource.UPLOAD,
                source_reference=f"upload:{batch_id}:{position}",
                source_metadata={
                    "batch_id": batch_id,
                    "batch_position": str(position),
                    "batch_size": str(len(raw)),
                },
            )


class IngressGateway:
    """The single door into the pipeline.

    Responsibilities that must be identical for every source, and therefore live
    here rather than in adapters:

    * validate the payload (media type, size, non-empty) — enforced by
      `DocumentPayload` construction;
    * reject duplicate content before any model is invoked (FR-1.4);
    * store the bytes content-addressed;
    * record the `documents` row and an audit entry.

    Dedupe runs *first* because it is the cheapest possible rejection: a
    resubmitted file should cost a hash comparison, not a parse and an LLM call.
    """

    def __init__(self, store: DocumentStore) -> None:
        self._store = store

    def submit(
        self,
        session: Session,
        adapter: IngestionAdapter,
        raw: object,
        *,
        allow_duplicate: bool = False,
    ) -> BatchIngressResult:
        """Run an adapter's output through the shared ingress path."""
        results: list[IngressResult] = []
        # Track hashes seen within this batch: the same file twice in one upload
        # must be caught even though neither is committed yet.
        seen_in_batch: dict[str, str] = {}

        for event in adapter.to_events(raw):
            results.append(
                self._ingest_one(
                    session, event, seen_in_batch, allow_duplicate=allow_duplicate
                )
            )

        return BatchIngressResult(results=tuple(results))

    def _ingest_one(
        self,
        session: Session,
        event: InvoiceReceived,
        seen_in_batch: dict[str, str],
        *,
        allow_duplicate: bool = False,
    ) -> IngressResult:
        digest = event.content_hash

        if digest in seen_in_batch:
            if allow_duplicate:
                return IngressResult.accept(event, seen_in_batch[digest])
            return IngressResult.reject(
                IngressRejection.DUPLICATE_CONTENT,
                (
                    f"{event.payload.filename!r} is byte-identical to another file "
                    "in this same submission."
                ),
                existing_document_id=seen_in_batch[digest],
            )

        existing = session.scalar(
            select(Document).where(
                Document.tenant_id == event.tenant_id,
                Document.content_hash == digest,
            )
        )
        if existing is not None:
            if allow_duplicate:
                # Re-use the stored document row; a new run will still be created
                # upstream so the user can re-process the same bytes.
                return IngressResult.accept(event, existing.document_id)
            # Not an error: a resubmitted invoice is ordinary. Caught here so it
            # costs a hash lookup rather than a parse and a model call.
            return IngressResult.reject(
                IngressRejection.DUPLICATE_CONTENT,
                (
                    f"{event.payload.filename!r} has already been ingested as "
                    f"{existing.filename!r}."
                ),
                existing_document_id=existing.document_id,
            )

        reference = self._store.put(
            event.payload.content, media_type=event.payload.media_type
        )

        document = Document(
            tenant_id=event.tenant_id,
            source=event.source.value,
            filename=event.payload.filename,
            media_type=event.payload.media_type,
            size_bytes=event.payload.size_bytes,
            content_hash=digest,
            storage_path=reference,
        )
        session.add(document)
        session.flush()

        session.add(
            AuditLog(
                tenant_id=event.tenant_id,
                entity_type="document",
                entity_id=document.document_id,
                event="ingested",
                actor=f"adapter:{event.source.value}",
                detail={
                    "filename": event.payload.filename,
                    "media_type": event.payload.media_type,
                    "size_bytes": event.payload.size_bytes,
                    "content_hash": digest,
                    "source_reference": event.source_reference,
                    "source_metadata": event.source_metadata,
                },
                occurred_at=event.received_at,
            )
        )

        seen_in_batch[digest] = document.document_id
        return IngressResult.accept(event, document.document_id)


# --------------------------------------------------------------- v2 adapters

# These exist so the seam is demonstrably real. Each raises rather than pretending
# to work — a stub that silently returned nothing would be worse than one that
# refuses, because the pipeline would look healthy while dropping invoices.


class _NotYetImplementedAdapter:
    """Base for v2 adapters. Conforms to the protocol; refuses to run."""

    _source: InvoiceSource
    _name: str
    _v2_task: str

    @property
    def source(self) -> InvoiceSource:
        return self._source

    def to_events(self, raw: object) -> Iterable[InvoiceReceived]:
        raise NotImplementedError(
            f"{self._name} is a v2 integration ({self._v2_task}). "
            "It conforms to IngestionAdapter so the gateway needs no change when "
            "it lands, but it will not silently accept documents before then."
        )


class EmailIntakeAdapter(_NotYetImplementedAdapter):
    """AP mailbox intake — the commonest real-world source (v2.1)."""

    _source = InvoiceSource.EMAIL
    _name = "EmailIntakeAdapter"
    _v2_task = "v2.1"


class ErpWebhookAdapter(_NotYetImplementedAdapter):
    """Invoices pushed by the customer's ERP (v2.2)."""

    _source = InvoiceSource.ERP_WEBHOOK
    _name = "ErpWebhookAdapter"
    _v2_task = "v2.2"


class BucketWatcherAdapter(_NotYetImplementedAdapter):
    """Watched object store or SFTP drop (v2.3)."""

    _source = InvoiceSource.BUCKET
    _name = "BucketWatcherAdapter"
    _v2_task = "v2.3"


V2_ADAPTERS: tuple[type[_NotYetImplementedAdapter], ...] = (
    EmailIntakeAdapter,
    ErpWebhookAdapter,
    BucketWatcherAdapter,
)
