"""The normalised ingress event.

Every source — an upload today, an AP mailbox or ERP webhook tomorrow — converges
on one `InvoiceReceived` event before anything downstream runs (FR-1.2). The
pipeline therefore never learns how a document arrived, which is what makes v2's
real integrations a connector change rather than a pipeline change (FR-1.3).

The event carries the raw bytes plus provenance about *how* it arrived. It does
not carry parsed content: parsing is a separate, expensive, failure-prone step
and conflating the two would make the ingress boundary untestable.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ap_agent.core.canonical import InvoiceSource

# Media types we accept. Deliberately a short allow-list: an unexpected type is a
# rejection, not a "try it and see". A .docx invoice is a real thing but it is a
# v2 concern, and silently attempting it would produce a confusing parse failure
# deep in the pipeline instead of a clear rejection at the door.
ACCEPTED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
    }
)

# 25 MiB. Large enough for a multi-page scanned invoice at 300 dpi, small enough
# that a malformed or hostile upload cannot exhaust memory.
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


class IngressRejection(StrEnum):
    """Why a document was refused at the door.

    Rejections are named so the UI can explain them and so metrics can
    distinguish "the sender keeps mailing us spreadsheets" from "someone is
    uploading 40 MB scans".
    """

    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    TOO_LARGE = "too_large"
    EMPTY = "empty"
    DUPLICATE_CONTENT = "duplicate_content"


class DocumentPayload(BaseModel):
    """Raw bytes plus the minimum needed to identify them.

    `content_hash` is computed here rather than accepted from the caller: a
    caller-supplied hash is an assertion, and dedupe is only trustworthy if the
    hash is derived from the bytes we actually hold.
    """

    model_config = ConfigDict(frozen=True)

    filename: str = Field(min_length=1, max_length=512)
    media_type: str
    content: bytes

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not self.content:
            raise ValueError("Document payload is empty.")
        if len(self.content) > MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"Document is {len(self.content)} bytes, over the "
                f"{MAX_DOCUMENT_BYTES} byte limit."
            )
        if self.media_type not in ACCEPTED_MEDIA_TYPES:
            raise ValueError(
                f"Unsupported media type {self.media_type!r}. "
                f"Accepted: {', '.join(sorted(ACCEPTED_MEDIA_TYPES))}."
            )
        return self

    @property
    def content_hash(self) -> str:
        """SHA-256 of the bytes. The dedupe key (FR-1.4)."""
        return hashlib.sha256(self.content).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(self.content)

    @property
    def is_image(self) -> bool:
        return self.media_type.startswith("image/")

    def __repr__(self) -> str:
        # Never repr the bytes: an invoice is customer data and this ends up in
        # logs and tracebacks.
        return (
            f"DocumentPayload(filename={self.filename!r}, "
            f"media_type={self.media_type!r}, size_bytes={self.size_bytes}, "
            f"content_hash={self.content_hash[:12]}...)"
        )


class InvoiceReceived(BaseModel):
    """A document has arrived and is ready to be processed.

    The single entry point to the pipeline. `source` records which adapter
    produced it, and `source_reference` keeps the adapter-specific handle (message
    id, S3 key, webhook delivery id) so a run can be traced back to its origin
    without the pipeline having to understand those systems.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1)
    payload: DocumentPayload
    source: InvoiceSource
    source_reference: str | None = Field(
        default=None,
        max_length=512,
        description="Adapter-specific origin handle, e.g. an email Message-ID.",
    )
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Free-form adapter metadata (sender address, bucket, batch id). Kept opaque
    # so adding an adapter needs no schema change here.
    source_metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return self.payload.content_hash


class IngressResult(BaseModel):
    """Outcome of offering a document to the ingress layer.

    Rejection is an ordinary, expected outcome — a duplicate upload is not an
    error — so it is modelled as a result rather than an exception. Genuine
    failures (storage unreachable) still raise.
    """

    model_config = ConfigDict(frozen=True)

    accepted: bool
    event: InvoiceReceived | None = None
    document_id: str | None = None
    rejection: IngressRejection | None = None
    detail: str | None = None
    # Set when a duplicate is rejected, so the UI can link to the original.
    existing_document_id: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.accepted:
            if self.event is None or self.document_id is None:
                raise ValueError("An accepted result must carry an event and a document_id.")
            if self.rejection is not None:
                raise ValueError("An accepted result cannot also carry a rejection.")
        else:
            if self.rejection is None:
                raise ValueError("A rejected result must state a rejection reason.")
            if self.detail is None:
                raise ValueError(
                    "A rejected result must explain itself: the reason is shown to a user."
                )
        return self

    @classmethod
    def accept(cls, event: InvoiceReceived, document_id: str) -> IngressResult:
        return cls(accepted=True, event=event, document_id=document_id)

    @classmethod
    def reject(
        cls,
        rejection: IngressRejection,
        detail: str,
        *,
        existing_document_id: str | None = None,
    ) -> IngressResult:
        return cls(
            accepted=False,
            rejection=rejection,
            detail=detail,
            existing_document_id=existing_document_id,
        )


class BatchIngressResult(BaseModel):
    """Outcome of a multi-document submission.

    Per-document results, not an all-or-nothing outcome: one duplicate in a batch
    of forty must not discard the other thirty-nine.
    """

    model_config = ConfigDict(frozen=True)

    results: tuple[IngressResult, ...]

    @property
    def accepted(self) -> tuple[IngressResult, ...]:
        return tuple(r for r in self.results if r.accepted)

    @property
    def rejected(self) -> tuple[IngressResult, ...]:
        return tuple(r for r in self.results if not r.accepted)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def summary(self) -> str:
        return f"{self.accepted_count} accepted, {self.rejected_count} rejected"
