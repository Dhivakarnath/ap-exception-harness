"""Ingress validation and event shape.

The door is where a malformed or hostile document should be stopped, so these
tests are mostly about what gets *refused*.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from ap_agent.core.canonical import InvoiceSource
from ap_agent.ingest.events import (
    ACCEPTED_MEDIA_TYPES,
    MAX_DOCUMENT_BYTES,
    BatchIngressResult,
    DocumentPayload,
    IngressRejection,
    IngressResult,
    InvoiceReceived,
)

PDF_BYTES = b"%PDF-1.4 fake body"


def _payload(**overrides: object) -> DocumentPayload:
    base: dict[str, object] = {
        "filename": "invoice.pdf",
        "media_type": "application/pdf",
        "content": PDF_BYTES,
    }
    return DocumentPayload(**{**base, **overrides})  # type: ignore[arg-type]


class TestPayloadValidation:
    def test_accepts_a_normal_pdf(self) -> None:
        p = _payload()
        assert p.size_bytes == len(PDF_BYTES)
        assert p.is_image is False

    def test_rejects_empty_content(self) -> None:
        with pytest.raises(ValidationError, match="empty"):
            _payload(content=b"")

    def test_rejects_oversized_content(self) -> None:
        with pytest.raises(ValidationError, match="over the"):
            _payload(content=b"x" * (MAX_DOCUMENT_BYTES + 1))

    def test_rejects_unsupported_media_type(self) -> None:
        # A spreadsheet invoice is real but is a v2 concern. Refusing at the door
        # beats a confusing parse failure deep in the pipeline.
        with pytest.raises(ValidationError, match="Unsupported media type"):
            _payload(media_type="application/vnd.ms-excel")

    @pytest.mark.parametrize("media_type", sorted(ACCEPTED_MEDIA_TYPES))
    def test_every_accepted_media_type_constructs(self, media_type: str) -> None:
        assert _payload(media_type=media_type).media_type == media_type

    def test_image_types_are_flagged_as_images(self) -> None:
        assert _payload(media_type="image/png", content=b"\x89PNG").is_image is True

    def test_payload_is_immutable(self) -> None:
        with pytest.raises(ValidationError):
            _payload().filename = "other.pdf"  # type: ignore[misc]


class TestContentHash:
    def test_hash_is_derived_from_the_bytes(self) -> None:
        # Computed, never accepted from a caller: dedupe is only trustworthy if
        # the hash comes from the bytes we actually hold.
        assert _payload().content_hash == hashlib.sha256(PDF_BYTES).hexdigest()

    def test_identical_bytes_hash_identically_regardless_of_filename(self) -> None:
        a = _payload(filename="a.pdf")
        b = _payload(filename="totally-different-name.pdf")
        assert a.content_hash == b.content_hash

    def test_different_bytes_hash_differently(self) -> None:
        assert _payload().content_hash != _payload(content=PDF_BYTES + b"!").content_hash


class TestPayloadRepr:
    def test_repr_does_not_leak_document_bytes(self) -> None:
        # Reprs land in logs and tracebacks; an invoice is customer data.
        secret = b"%PDF SENSITIVE-VENDOR-BANK-DETAILS"
        text = repr(_payload(content=secret))
        assert "SENSITIVE" not in text
        assert "invoice.pdf" in text
        assert "size_bytes" in text


class TestInvoiceReceived:
    def test_carries_source_provenance(self) -> None:
        event = InvoiceReceived(
            tenant_id="t1",
            payload=_payload(),
            source=InvoiceSource.UPLOAD,
            source_reference="upload:batch1:1",
            source_metadata={"batch_id": "batch1"},
        )
        assert event.content_hash == _payload().content_hash
        assert event.source_reference == "upload:batch1:1"
        assert event.source_metadata["batch_id"] == "batch1"

    def test_received_at_defaults_to_now(self) -> None:
        event = InvoiceReceived(
            tenant_id="t1", payload=_payload(), source=InvoiceSource.UPLOAD
        )
        assert event.received_at.tzinfo is not None


class TestIngressResult:
    def test_accept_requires_event_and_document_id(self) -> None:
        with pytest.raises(ValidationError, match="must carry an event"):
            IngressResult(accepted=True)

    def test_reject_requires_a_reason(self) -> None:
        with pytest.raises(ValidationError, match="must state a rejection reason"):
            IngressResult(accepted=False)

    def test_reject_requires_a_human_readable_detail(self) -> None:
        # The detail is shown to a user, so an unexplained rejection is a bug.
        with pytest.raises(ValidationError, match="must explain itself"):
            IngressResult(accepted=False, rejection=IngressRejection.TOO_LARGE)

    def test_accepted_result_cannot_also_be_rejected(self) -> None:
        event = InvoiceReceived(
            tenant_id="t1", payload=_payload(), source=InvoiceSource.UPLOAD
        )
        with pytest.raises(ValidationError, match="cannot also carry a rejection"):
            IngressResult(
                accepted=True,
                event=event,
                document_id="doc-1",
                rejection=IngressRejection.DUPLICATE_CONTENT,
                detail="?",
            )

    def test_duplicate_rejection_links_to_the_original(self) -> None:
        result = IngressResult.reject(
            IngressRejection.DUPLICATE_CONTENT,
            "already ingested",
            existing_document_id="doc-original",
        )
        assert result.existing_document_id == "doc-original"


class TestBatchResult:
    def test_partitions_accepted_and_rejected(self) -> None:
        event = InvoiceReceived(
            tenant_id="t1", payload=_payload(), source=InvoiceSource.UPLOAD
        )
        batch = BatchIngressResult(
            results=(
                IngressResult.accept(event, "doc-1"),
                IngressResult.reject(IngressRejection.DUPLICATE_CONTENT, "dupe"),
                IngressResult.accept(event, "doc-2"),
            )
        )
        assert batch.accepted_count == 2
        assert batch.rejected_count == 1
        assert batch.summary() == "2 accepted, 1 rejected"

    def test_empty_batch_is_valid(self) -> None:
        assert BatchIngressResult(results=()).accepted_count == 0
