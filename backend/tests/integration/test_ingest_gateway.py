"""Ingress gateway: dedupe, persistence, and the adapter seam.

Marked `integration`: needs Postgres and an applied migration.

The central assertion is that duplicate content is rejected **before** anything
expensive happens. Dedupe that ran after parsing would still prevent a double
payment, but it would pay the parse and model cost on every resubmission — and
resubmissions are routine in AP.
"""

from __future__ import annotations

import pytest

from ap_agent.core.canonical import InvoiceSource
from ap_agent.ingest.adapters import (
    V2_ADAPTERS,
    BucketWatcherAdapter,
    EmailIntakeAdapter,
    ErpWebhookAdapter,
    IngestionAdapter,
    IngressGateway,
    UploadAdapter,
)
from ap_agent.ingest.events import DocumentPayload, IngressRejection
from ap_agent.ingest.storage import InMemoryDocumentStore
from ap_agent.persistence.db import session_scope
from ap_agent.persistence.models import AuditLog, Document, Tenant

pytestmark = pytest.mark.integration

TENANT = "ingest-test-tenant"


@pytest.fixture
def tenant() -> str:
    """Isolated tenant.

    `audit_log` is cleared explicitly. It deliberately has **no** foreign key to
    `tenants`, because an append-only audit trail must survive the deletion of the
    thing it describes — otherwise removing a tenant would erase the evidence of
    what was done on their behalf. The trade-off is that tests must clean it by
    hand rather than relying on a cascade.
    """
    _purge(TENANT)
    with session_scope() as s:
        s.add(
            Tenant(
                tenant_id=TENANT,
                name="Ingest Test",
                industry="test",
                base_currency="USD",
                active_policy_version="1.0.0",
            )
        )
    yield TENANT
    _purge(TENANT)


def _purge(tenant_id: str) -> None:
    with session_scope() as s:
        # Cascades clear documents; audit_log has no FK by design.
        s.query(Tenant).filter_by(tenant_id=tenant_id).delete()
        s.query(AuditLog).filter_by(tenant_id=tenant_id).delete()


def _payload(name: str, body: bytes) -> DocumentPayload:
    return DocumentPayload(filename=name, media_type="application/pdf", content=body)


class TestUploadAdapter:
    def test_conforms_to_the_protocol(self) -> None:
        assert isinstance(UploadAdapter(TENANT), IngestionAdapter)

    def test_reports_its_source(self) -> None:
        assert UploadAdapter(TENANT).source is InvoiceSource.UPLOAD

    def test_single_upload_produces_one_event(self) -> None:
        events = list(UploadAdapter(TENANT).to_events([_payload("a.pdf", b"%PDF a")]))
        assert len(events) == 1
        assert events[0].tenant_id == TENANT
        assert events[0].source is InvoiceSource.UPLOAD

    def test_batch_upload_tags_positions(self) -> None:
        payloads = [_payload(f"{i}.pdf", f"%PDF {i}".encode()) for i in range(3)]
        events = list(UploadAdapter(TENANT).to_events(payloads))

        assert [e.source_metadata["batch_position"] for e in events] == ["1", "2", "3"]
        assert {e.source_metadata["batch_size"] for e in events} == {"3"}
        # One batch id shared across the submission.
        assert len({e.source_metadata["batch_id"] for e in events}) == 1

    def test_rejects_a_non_sequence_input(self) -> None:
        with pytest.raises(TypeError, match="expects a sequence"):
            list(UploadAdapter(TENANT).to_events("not-a-list"))

    def test_rejects_wrong_item_type(self) -> None:
        with pytest.raises(TypeError, match="expected DocumentPayload"):
            list(UploadAdapter(TENANT).to_events([b"raw bytes"]))


class TestGatewayAcceptance:
    def test_accepts_and_persists_a_document(self, tenant: str) -> None:
        store = InMemoryDocumentStore()
        gateway = IngressGateway(store)

        with session_scope() as s:
            batch = gateway.submit(
                s, UploadAdapter(tenant), [_payload("inv.pdf", b"%PDF unique-1")]
            )

        assert batch.accepted_count == 1
        document_id = batch.accepted[0].document_id
        assert document_id is not None

        with session_scope() as s:
            row = s.get(Document, document_id)
            assert row is not None
            assert row.filename == "inv.pdf"
            assert row.source == "upload"
            assert row.size_bytes == len(b"%PDF unique-1")
            assert store.exists(row.storage_path)

    def test_writes_an_audit_entry(self, tenant: str) -> None:
        gateway = IngressGateway(InMemoryDocumentStore())
        with session_scope() as s:
            gateway.submit(s, UploadAdapter(tenant), [_payload("a.pdf", b"%PDF audit")])

        with session_scope() as s:
            entries = (
                s.query(AuditLog)
                .filter_by(tenant_id=tenant, entity_type="document", event="ingested")
                .all()
            )
        assert len(entries) == 1
        assert entries[0].actor == "adapter:upload"
        assert entries[0].detail is not None
        assert entries[0].detail["content_hash"]

    def test_batch_accepts_all_distinct_documents(self, tenant: str) -> None:
        gateway = IngressGateway(InMemoryDocumentStore())
        payloads = [_payload(f"{i}.pdf", f"%PDF distinct {i}".encode()) for i in range(5)]

        with session_scope() as s:
            batch = gateway.submit(s, UploadAdapter(tenant), payloads)

        assert batch.accepted_count == 5
        assert batch.rejected_count == 0


class TestGatewayDedupe:
    def test_resubmitting_identical_content_is_rejected(self, tenant: str) -> None:
        gateway = IngressGateway(InMemoryDocumentStore())
        body = b"%PDF resubmitted"

        with session_scope() as s:
            first = gateway.submit(s, UploadAdapter(tenant), [_payload("a.pdf", body)])

        with session_scope() as s:
            second = gateway.submit(s, UploadAdapter(tenant), [_payload("a.pdf", body)])

        assert first.accepted_count == 1
        assert second.rejected_count == 1
        rejection = second.rejected[0]
        assert rejection.rejection is IngressRejection.DUPLICATE_CONTENT
        assert rejection.existing_document_id == first.accepted[0].document_id

    def test_dedupe_ignores_the_filename(self, tenant: str) -> None:
        # The same invoice mailed twice under different names is still the same
        # invoice.
        gateway = IngressGateway(InMemoryDocumentStore())
        body = b"%PDF same bytes"

        with session_scope() as s:
            gateway.submit(s, UploadAdapter(tenant), [_payload("original.pdf", body)])
        with session_scope() as s:
            again = gateway.submit(
                s, UploadAdapter(tenant), [_payload("forwarded-copy.pdf", body)]
            )

        assert again.rejected_count == 1

    def test_duplicate_within_a_single_batch_is_caught(self, tenant: str) -> None:
        # Neither row is committed yet, so an in-batch check is required.
        gateway = IngressGateway(InMemoryDocumentStore())
        body = b"%PDF twice in one batch"

        with session_scope() as s:
            batch = gateway.submit(
                s,
                UploadAdapter(tenant),
                [_payload("a.pdf", body), _payload("b.pdf", body)],
            )

        assert batch.accepted_count == 1
        assert batch.rejected_count == 1
        assert batch.rejected[0].rejection is IngressRejection.DUPLICATE_CONTENT

    def test_one_duplicate_does_not_discard_the_rest_of_the_batch(self, tenant: str) -> None:
        gateway = IngressGateway(InMemoryDocumentStore())
        dupe = b"%PDF dupe"

        with session_scope() as s:
            gateway.submit(s, UploadAdapter(tenant), [_payload("seen.pdf", dupe)])

        with session_scope() as s:
            batch = gateway.submit(
                s,
                UploadAdapter(tenant),
                [
                    _payload("new-1.pdf", b"%PDF new 1"),
                    _payload("dupe.pdf", dupe),
                    _payload("new-2.pdf", b"%PDF new 2"),
                ],
            )

        assert batch.accepted_count == 2
        assert batch.rejected_count == 1

    def test_same_content_is_allowed_for_a_different_tenant(self, tenant: str) -> None:
        # Dedupe is scoped per tenant: two customers may legitimately receive
        # byte-identical documents, and cross-tenant coupling would be a leak.
        other = "ingest-test-tenant-2"
        gateway = IngressGateway(InMemoryDocumentStore())
        body = b"%PDF shared across tenants"

        with session_scope() as s:
            s.add(
                Tenant(
                    tenant_id=other,
                    name="Other",
                    industry="test",
                    base_currency="USD",
                    active_policy_version="1.0.0",
                )
            )

        try:
            with session_scope() as s:
                a = gateway.submit(s, UploadAdapter(tenant), [_payload("x.pdf", body)])
            with session_scope() as s:
                b = gateway.submit(s, UploadAdapter(other), [_payload("x.pdf", body)])

            assert a.accepted_count == 1
            assert b.accepted_count == 1
        finally:
            _purge(other)

    def test_storage_is_not_written_twice_for_a_duplicate(self, tenant: str) -> None:
        store = InMemoryDocumentStore()
        gateway = IngressGateway(store)
        body = b"%PDF stored once"

        with session_scope() as s:
            first = gateway.submit(s, UploadAdapter(tenant), [_payload("a.pdf", body)])
        with session_scope() as s:
            gateway.submit(s, UploadAdapter(tenant), [_payload("a.pdf", body)])

        with session_scope() as s:
            row = s.get(Document, first.accepted[0].document_id)
        assert row is not None
        assert store.get(row.storage_path) == body


class TestV2AdapterSeam:
    """The v2 adapters exist so the seam is demonstrably real, not aspirational."""

    @pytest.mark.parametrize("adapter_cls", V2_ADAPTERS)
    def test_conforms_to_the_protocol(self, adapter_cls: type) -> None:
        # The gateway needs no change to accept these when they land.
        assert isinstance(adapter_cls(), IngestionAdapter)

    @pytest.mark.parametrize("adapter_cls", V2_ADAPTERS)
    def test_refuses_rather_than_silently_accepting(self, adapter_cls: type) -> None:
        # A stub that quietly returned nothing would look healthy while dropping
        # invoices — far worse than one that refuses.
        with pytest.raises(NotImplementedError, match="v2 integration"):
            list(adapter_cls().to_events(None))

    def test_each_declares_a_distinct_source(self) -> None:
        assert EmailIntakeAdapter().source is InvoiceSource.EMAIL
        assert ErpWebhookAdapter().source is InvoiceSource.ERP_WEBHOOK
        assert BucketWatcherAdapter().source is InvoiceSource.BUCKET

    def test_all_ingress_sources_have_an_adapter(self) -> None:
        # If a source exists in the canonical enum with no adapter, the ingress
        # layer has a hole.
        covered = {UploadAdapter(TENANT).source} | {a().source for a in V2_ADAPTERS}
        assert covered == set(InvoiceSource)
