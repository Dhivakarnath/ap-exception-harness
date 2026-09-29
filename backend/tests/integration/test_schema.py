"""Schema-level guarantees.

Marked `integration`: requires the Postgres service and an applied migration.

The important tests here are the CHECK constraints. Segregation of duties and
the auto-approve/human contradiction are enforced in the domain model *and* at
the storage boundary — deliberately twice, because the `decisions` row is the
audit evidence. A domain-layer bug must not be able to write an invalid
authority record.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ap_agent.persistence.db import get_engine, session_scope, verify_extensions
from ap_agent.persistence.models import (
    CheckResultRow,
    DecisionRow,
    Document,
    Invoice,
    PolicyChunk,
    Run,
    Tenant,
    Vendor,
)

pytestmark = pytest.mark.integration

TENANT = "schema-test-tenant"


@pytest.fixture
def clean_tenant() -> str:
    """Create an isolated tenant and remove it afterwards."""
    with session_scope() as s:
        s.query(Tenant).filter_by(tenant_id=TENANT).delete()
    with session_scope() as s:
        s.add(
            Tenant(
                tenant_id=TENANT,
                name="Schema Test",
                industry="test",
                base_currency="USD",
                active_policy_version="1.0.0",
            )
        )
    yield TENANT
    with session_scope() as s:
        # Cascades clear the dependent rows.
        s.query(Tenant).filter_by(tenant_id=TENANT).delete()


def _seed_run(session, tenant: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    doc = Document(
        tenant_id=tenant,
        source="upload",
        filename="inv.pdf",
        media_type="application/pdf",
        size_bytes=1024,
        content_hash=f"hash-{datetime.now(UTC).timestamp()}",
        storage_path="/tmp/inv.pdf",
    )
    session.add(doc)
    session.flush()

    inv = Invoice(
        tenant_id=tenant,
        document_id=doc.document_id,
        source="upload",
        invoice_number="INV-001",
        invoice_date=date(2026, 1, 15),
        vendor_name_raw="Acme Corp",
        currency="USD",
        subtotal=Decimal("100.0000"),
        total_amount=Decimal("110.0000"),
        header_confidence=0.97,
        overall_confidence=0.95,
    )
    session.add(inv)
    session.flush()

    run = Run(
        tenant_id=tenant,
        invoice_id=inv.invoice_id,
        policy_version="1.0.0",
        status="completed",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    return run.run_id, inv.invoice_id


class TestExtensions:
    def test_required_extensions_present(self) -> None:
        found = verify_extensions()
        assert "vector" in found
        assert "pg_trgm" in found


class TestSegregationOfDutiesConstraint:
    def test_agent_cannot_be_recorded_as_approver(self, clean_tenant: str) -> None:
        # FR-4.11 at the storage boundary. Even if domain validation were
        # bypassed, the database refuses the row.
        with pytest.raises(IntegrityError, match="ck_decisions_sod_agent_not_approver"), session_scope() as s:
                run_id, invoice_id = _seed_run(s, clean_tenant)
                s.add(
                    DecisionRow(
                        run_id=run_id,
                        tenant_id=clean_tenant,
                        invoice_id=invoice_id,
                        route="route_for_approval",
                        decided_by="agent",
                        rationale="attempting to self-approve",
                        decided_at=datetime.now(UTC),
                        approver_identity="agent",
                    )
                )

    def test_agent_may_route_without_naming_approver(self, clean_tenant: str) -> None:
        with session_scope() as s:
            run_id, invoice_id = _seed_run(s, clean_tenant)
            s.add(
                DecisionRow(
                    run_id=run_id,
                    tenant_id=clean_tenant,
                    invoice_id=invoice_id,
                    route="route_for_approval",
                    decided_by="agent",
                    rationale="amount exceeds touchless threshold",
                    decided_at=datetime.now(UTC),
                    required_approver_tier="controller",
                )
            )

    def test_human_cannot_record_auto_approval(self, clean_tenant: str) -> None:
        with pytest.raises(IntegrityError, match="ck_decisions_auto_approve_not_human"), session_scope() as s:
                run_id, invoice_id = _seed_run(s, clean_tenant)
                s.add(
                    DecisionRow(
                        run_id=run_id,
                        tenant_id=clean_tenant,
                        invoice_id=invoice_id,
                        route="auto_approve",
                        decided_by="human",
                        rationale="looked fine",
                        decided_at=datetime.now(UTC),
                    )
                )

    def test_policy_engine_auto_approval_is_allowed(self, clean_tenant: str) -> None:
        with session_scope() as s:
            run_id, invoice_id = _seed_run(s, clean_tenant)
            s.add(
                DecisionRow(
                    run_id=run_id,
                    tenant_id=clean_tenant,
                    invoice_id=invoice_id,
                    route="auto_approve",
                    decided_by="policy_engine",
                    rationale="clean three-way match under touchless threshold",
                    decided_at=datetime.now(UTC),
                )
            )

    def test_invalid_route_is_rejected(self, clean_tenant: str) -> None:
        with pytest.raises(IntegrityError, match="ck_decisions_route"), session_scope() as s:
                run_id, invoice_id = _seed_run(s, clean_tenant)
                s.add(
                    DecisionRow(
                        run_id=run_id,
                        tenant_id=clean_tenant,
                        invoice_id=invoice_id,
                        route="pay_immediately",
                        decided_by="policy_engine",
                        rationale="nope",
                        decided_at=datetime.now(UTC),
                    )
                )


class TestMoneyPrecision:
    def test_numeric_storage_is_exact(self, clean_tenant: str) -> None:
        # The storage-boundary half of the no-float rule: a float column would
        # reintroduce the rounding error the domain layer forbids.
        with session_scope() as s:
            run_id, invoice_id = _seed_run(s, clean_tenant)
            assert run_id

        with session_scope() as s:
            inv = s.get(Invoice, invoice_id)
            assert inv is not None
            assert isinstance(inv.total_amount, Decimal)
            assert inv.total_amount == Decimal("110.0000")

    def test_repeated_small_amounts_do_not_drift(self, clean_tenant: str) -> None:
        with session_scope() as s:
            doc = Document(
                tenant_id=clean_tenant,
                source="upload",
                filename="d.pdf",
                media_type="application/pdf",
                size_bytes=1,
                content_hash="drift-test",
                storage_path="/tmp/d.pdf",
            )
            s.add(doc)
            s.flush()
            for i in range(100):
                s.add(
                    Invoice(
                        tenant_id=clean_tenant,
                        document_id=doc.document_id,
                        source="upload",
                        invoice_number=f"DRIFT-{i}",
                        invoice_date=date(2026, 1, 1),
                        vendor_name_raw="V",
                        currency="USD",
                        subtotal=Decimal("0.0100"),
                        total_amount=Decimal("0.0100"),
                        header_confidence=1.0,
                        overall_confidence=1.0,
                    )
                )

        with session_scope() as s:
            total = s.execute(
                text(
                    "SELECT SUM(total_amount) FROM invoices "
                    "WHERE tenant_id = :t AND invoice_number LIKE 'DRIFT-%'"
                ),
                {"t": clean_tenant},
            ).scalar_one()
        assert total == Decimal("1.0000")


class TestDedupeAndUniqueness:
    def test_duplicate_content_hash_is_rejected(self, clean_tenant: str) -> None:
        # Ingress dedupe (FR-1.4): the same file twice is caught before any
        # model is invoked.
        with session_scope() as s:
            s.add(
                Document(
                    tenant_id=clean_tenant,
                    source="upload",
                    filename="a.pdf",
                    media_type="application/pdf",
                    size_bytes=1,
                    content_hash="identical",
                    storage_path="/tmp/a.pdf",
                )
            )

        with pytest.raises(IntegrityError, match="uq_document_content_hash"), session_scope() as s:
                s.add(
                    Document(
                        tenant_id=clean_tenant,
                        source="upload",
                        filename="b.pdf",
                        media_type="application/pdf",
                        size_bytes=1,
                        content_hash="identical",
                        storage_path="/tmp/b.pdf",
                    )
                )


class TestCheckResultRows:
    def test_check_results_are_queryable_rows(self, clean_tenant: str) -> None:
        # Rows rather than a JSON blob, so KPIs are SQL questions (FR-12.6).
        with session_scope() as s:
            run_id, _ = _seed_run(s, clean_tenant)
            for i, (name, verdict) in enumerate(
                [
                    ("completeness", "pass"),
                    ("math_integrity", "pass"),
                    ("three_way_match", "fail"),
                    ("threshold_avoidance", "flag"),
                ]
            ):
                s.add(
                    CheckResultRow(
                        run_id=run_id,
                        tenant_id=clean_tenant,
                        sequence=i,
                        name=name,
                        category="matching",
                        verdict=verdict,
                        severity="medium" if verdict != "pass" else "info",
                        reasoning=f"{name} evaluated",
                        forces_review=verdict == "flag",
                        evaluated_at=datetime.now(UTC),
                    )
                )

        with session_scope() as s:
            failures = s.execute(
                text(
                    "SELECT name FROM check_results "
                    "WHERE tenant_id = :t AND verdict = 'fail'"
                ),
                {"t": clean_tenant},
            ).scalars().all()
        assert failures == ["three_way_match"]

    def test_invalid_verdict_is_rejected(self, clean_tenant: str) -> None:
        with pytest.raises(IntegrityError, match="ck_check_results_verdict"), session_scope() as s:
                run_id, _ = _seed_run(s, clean_tenant)
                s.add(
                    CheckResultRow(
                        run_id=run_id,
                        tenant_id=clean_tenant,
                        sequence=0,
                        name="x",
                        category="matching",
                        verdict="maybe",
                        severity="info",
                        reasoning="?",
                        evaluated_at=datetime.now(UTC),
                    )
                )


class TestVectorAndHybridColumns:
    def test_embedding_and_tsvector_coexist(self, clean_tenant: str) -> None:
        # One row carries both dense and lexical representations, so hybrid
        # retrieval needs no second datastore (ADR-008).
        with session_scope() as s:
            s.add(
                PolicyChunk(
                    tenant_id=clean_tenant,
                    doc_type="accounting_policy",
                    source_name="accounting_policy.md",
                    section="4.2 Office Supplies",
                    citation_ref="accounting_policy.md#4.2",
                    version="1.0.0",
                    effective_from=date(2026, 1, 1),
                    content="Office supplies and stationery are coded to GL 6410.",
                    embedding=[0.01] * 1024,
                )
            )

        with session_scope() as s:
            # Populate the lexical column the way the ingest pipeline will.
            s.execute(
                text(
                    "UPDATE policy_chunks SET content_tsv = to_tsvector('english', content) "
                    "WHERE tenant_id = :t"
                ),
                {"t": clean_tenant},
            )

        with session_scope() as s:
            lexical = s.execute(
                text(
                    "SELECT citation_ref FROM policy_chunks "
                    "WHERE tenant_id = :t AND content_tsv @@ plainto_tsquery('english', 'stationery')"
                ),
                {"t": clean_tenant},
            ).scalars().all()
            dense = s.execute(
                text(
                    "SELECT citation_ref FROM policy_chunks "
                    "WHERE tenant_id = :t ORDER BY embedding <=> :q LIMIT 1"
                ),
                {"t": clean_tenant, "q": str([0.01] * 1024)},
            ).scalars().all()

        assert lexical == ["accounting_policy.md#4.2"]
        assert dense == ["accounting_policy.md#4.2"]

    def test_invalid_doc_type_is_rejected(self, clean_tenant: str) -> None:
        with pytest.raises(IntegrityError, match="ck_policy_chunks_doc_type"), session_scope() as s:
                s.add(
                    PolicyChunk(
                        tenant_id=clean_tenant,
                        doc_type="random_notes",
                        source_name="x.md",
                        citation_ref="x.md#1",
                        version="1",
                        effective_from=date(2026, 1, 1),
                        content="...",
                    )
                )

    def test_effective_to_null_means_in_force(self, clean_tenant: str) -> None:
        # The metadata pre-filter relies on this to guarantee superseded policy
        # can never be cited (FR-6.3).
        with session_scope() as s:
            s.add_all(
                [
                    PolicyChunk(
                        tenant_id=clean_tenant,
                        doc_type="accounting_policy",
                        source_name="p.md",
                        citation_ref="p.md#v1",
                        version="1.0.0",
                        effective_from=date(2025, 1, 1),
                        effective_to=date(2025, 12, 31),
                        content="old rule",
                    ),
                    PolicyChunk(
                        tenant_id=clean_tenant,
                        doc_type="accounting_policy",
                        source_name="p.md",
                        citation_ref="p.md#v2",
                        version="2.0.0",
                        effective_from=date(2026, 1, 1),
                        content="current rule",
                    ),
                ]
            )

        with session_scope() as s:
            in_force = s.execute(
                text(
                    "SELECT citation_ref FROM policy_chunks "
                    "WHERE tenant_id = :t AND effective_from <= :d "
                    "AND (effective_to IS NULL OR effective_to >= :d)"
                ),
                {"t": clean_tenant, "d": date(2026, 6, 1)},
            ).scalars().all()
        assert in_force == ["p.md#v2"]


class TestTenantIsolation:
    def test_cascade_removes_dependent_rows(self, clean_tenant: str) -> None:
        with session_scope() as s:
            run_id, _ = _seed_run(s, clean_tenant)

        with session_scope() as s:
            s.query(Tenant).filter_by(tenant_id=clean_tenant).delete()

        with session_scope() as s:
            assert s.get(Run, run_id) is None

    def test_vendor_trigram_index_supports_fuzzy_lookup(self, clean_tenant: str) -> None:
        # Backs vendor entity resolution (FR-3.3) and near-duplicate vendor
        # fraud detection.
        with session_scope() as s:
            s.add_all(
                [
                    Vendor(
                        tenant_id=clean_tenant,
                        erp_vendor_id="V1",
                        legal_name="ACME Corporation",
                    ),
                    Vendor(
                        tenant_id=clean_tenant,
                        erp_vendor_id="V2",
                        legal_name="Globex Industries",
                    ),
                ]
            )

        with session_scope() as s:
            matches = s.execute(
                text(
                    "SELECT legal_name, similarity(legal_name, :q) AS sim "
                    "FROM vendors WHERE tenant_id = :t "
                    "AND similarity(legal_name, :q) > 0.3 ORDER BY sim DESC"
                ),
                {"t": clean_tenant, "q": "Acme Corp"},
            ).all()

        assert len(matches) == 1
        assert matches[0][0] == "ACME Corporation"


class TestMigrationReversibility:
    def test_engine_reports_expected_tables(self) -> None:
        expected = {
            "tenants",
            "vendors",
            "vendor_aliases",
            "field_aliases",
            "documents",
            "purchase_orders",
            "purchase_order_lines",
            "goods_receipts",
            "goods_receipt_lines",
            "invoices",
            "invoice_lines",
            "runs",
            "check_results",
            "decisions",
            "run_errors",
            "hitl_reviews",
            "erp_actions",
            "policy_chunks",
            "audit_log",
        }
        with get_engine().connect() as c:
            actual = set(
                c.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).scalars()
            )
        missing = expected - actual
        assert not missing, f"missing tables: {sorted(missing)}"
