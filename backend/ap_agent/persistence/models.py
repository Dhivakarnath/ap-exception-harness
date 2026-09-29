"""SQLAlchemy ORM models — the system of record.

The platform is both a *processor* (it acts in the customer's ERP) and a
*ledger* (it retains the auditable history of why). This module is the ledger
(FR-10.2).

Design commitments:

**Money is stored as NUMERIC, never floating point.** The same reason
`core.primitives.Money` refuses floats: a `double precision` column would
reintroduce binary rounding error at the storage boundary and quietly corrupt
the arithmetic checks.

**Check results are rows, not a JSON blob.** Every rule evaluation is queryable,
so "how often does the price-tolerance rule fire for this vendor?" is a SQL
question. Aggregated KPIs (FR-12.6) come from these rows.

**One database for relational data and vectors.** `policy_chunks` carries both a
`vector` column and a `tsvector` column, so hybrid retrieval happens in one
engine with tenant isolation as a native SQL predicate (ADR-008).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Monetary precision: 18 digits total, 4 decimal places. Four rather than two so
# unit prices with sub-cent precision survive storage without rounding; display
# quantisation to the currency's minor unit happens in the domain layer.
MONEY = Numeric(18, 4)
QUANTITY = Numeric(18, 6)
PERCENT = Numeric(9, 4)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------- tenancy


class Tenant(Base, TimestampMixin):
    """A customer. Rules live in a versioned policy pack, not in code."""

    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str] = mapped_column(String(64), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Which pack version is currently active. Decisions record the version they
    # were made under so they remain replayable.
    active_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --------------------------------------------------------------------- vendors


class Vendor(Base, TimestampMixin):
    """Vendor master, after entity resolution."""

    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "erp_vendor_id", name="uq_vendor_tenant_erp"),
        # Trigram index powers fuzzy name matching for entity resolution
        # (FR-3.3) and near-duplicate vendor fraud detection.
        Index(
            "ix_vendors_legal_name_trgm",
            "legal_name",
            postgresql_using="gin",
            postgresql_ops={"legal_name": "gin_trgm_ops"},
        ),
    )

    vendor_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    erp_vendor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(64))
    default_currency: Mapped[str | None] = mapped_column(String(3))
    default_payment_terms: Mapped[str | None] = mapped_column(String(32))

    # Only a fingerprint of remit-to details is retained: enough to detect a
    # change (FR-4.6), not enough to be worth stealing.
    bank_fingerprint: Mapped[str | None] = mapped_column(String(512))
    bank_fingerprint_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    aliases: Mapped[list[VendorAlias]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )


class VendorAlias(Base, TimestampMixin):
    """Name variants that resolve to one vendor.

    Learned semantically once, then enforced deterministically (ADR-009): a
    fuzzy match may *propose* an alias, but it is confirmed and persisted here so
    subsequent invoices resolve exactly and repeatably.
    """

    __tablename__ = "vendor_aliases"
    __table_args__ = (UniqueConstraint("tenant_id", "alias_norm", name="uq_vendor_alias_norm"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[str] = mapped_column(
        ForeignKey("vendors.vendor_id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_norm: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(255))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vendor: Mapped[Vendor] = relationship(back_populates="aliases")


class FieldAlias(Base, TimestampMixin):
    """Vendor invoice label -> canonical field binding (FR-3.1, FR-3.2).

    "Grand Total", "Amount Payable", and "Total Due" all mean `total_amount`.
    The first encounter may be proposed by the model; once confirmed it becomes a
    deterministic rule so the same vendor's next invoice needs no inference.
    """

    __tablename__ = "field_aliases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "vendor_id", "label_norm", name="uq_field_alias"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null vendor_id = a tenant-wide mapping rather than vendor-specific.
    vendor_id: Mapped[str | None] = mapped_column(
        ForeignKey("vendors.vendor_id", ondelete="CASCADE"), index=True
    )
    label_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    label_norm: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_field: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(255))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    times_applied: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ------------------------------------------------------------------- documents


class Document(Base, TimestampMixin):
    """A raw ingested file plus its parse artefacts."""

    __tablename__ = "documents"
    __table_args__ = (
        # Content-hash dedupe at ingress (FR-1.4): the same file arriving twice
        # is caught before any model is invoked.
        UniqueConstraint("tenant_id", "content_hash", name="uq_document_content_hash"),
    )

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    page_count: Mapped[int | None] = mapped_column(Integer)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parser_name: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    # Docling regions, retained so the UI can highlight a field back to its
    # source location on the page (FR-2.5).
    regions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    invoices: Mapped[list[Invoice]] = relationship(back_populates="document")


# -------------------------------------------------------- PO / GRN (ERP mirror)


class PurchaseOrder(Base, TimestampMixin):
    """Mirror of an ERP purchase order — what was ordered and authorised."""

    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "po_number", name="uq_po_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    po_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vendor_id: Mapped[str | None] = mapped_column(
        ForeignKey("vendors.vendor_id", ondelete="SET NULL"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str | None] = mapped_column(String(32))

    # Coding and authority attributes a PO-backed invoice inherits.
    gl_account: Mapped[str | None] = mapped_column(String(32))
    cost_center: Mapped[str | None] = mapped_column(String(64))
    department: Mapped[str | None] = mapped_column(String(64))
    entity: Mapped[str | None] = mapped_column(String(64))
    project: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    po_id: Mapped[str] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit_of_measure: Mapped[str | None] = mapped_column(String(32))
    gl_account: Mapped[str | None] = mapped_column(String(32))
    cost_center: Mapped[str | None] = mapped_column(String(64))

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class GoodsReceipt(Base, TimestampMixin):
    """Mirror of an ERP goods receipt — what was actually received."""

    __tablename__ = "goods_receipts"
    __table_args__ = (UniqueConstraint("tenant_id", "grn_number", name="uq_grn_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    grn_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    po_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    received_by: Mapped[str | None] = mapped_column(String(255))
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    lines: Mapped[list[GoodsReceiptLine]] = relationship(
        back_populates="goods_receipt", cascade="all, delete-orphan"
    )


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    grn_id: Mapped[str] = mapped_column(
        ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(64))
    quantity_received: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_of_measure: Mapped[str | None] = mapped_column(String(32))

    goods_receipt: Mapped[GoodsReceipt] = relationship(back_populates="lines")


# -------------------------------------------------------------------- invoices


class Invoice(Base, TimestampMixin):
    """Canonical extracted invoice.

    `*_confidence` columns are stored alongside values because confidence gates
    auto-approval (FR-11.4) and must be auditable after the fact, not recomputed.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_dup_lookup", "tenant_id", "vendor_id", "invoice_number"),
        Index("ix_invoices_amount_lookup", "tenant_id", "total_amount", "invoice_date"),
        # Trigram index for fuzzy near-duplicate invoice-number detection
        # (INV-001 vs INV-001A) — FR-4.3.
        Index(
            "ix_invoices_number_trgm",
            "invoice_number",
            postgresql_using="gin",
            postgresql_ops={"invoice_number": "gin_trgm_ops"},
        ),
    )

    invoice_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    invoice_number: Mapped[str] = mapped_column(String(128), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    vendor_name_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_id: Mapped[str | None] = mapped_column(
        ForeignKey("vendors.vendor_id", ondelete="SET NULL"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payment_terms: Mapped[str | None] = mapped_column(String(64))

    # Null = non-PO invoice, a meaningful business state that selects the
    # non-PO branch (FR-5.1), not missing data.
    po_reference: Mapped[str | None] = mapped_column(String(64), index=True)

    remit_to_fingerprint: Mapped[str | None] = mapped_column(String(512))

    header_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # Per-field confidence, method, and source region.
    field_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="invoices")
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="invoice")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.invoice_id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit_of_measure: Mapped[str | None] = mapped_column(String(32))
    min_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


# ------------------------------------------------------------------------ runs


class Run(Base, TimestampMixin):
    """One processing run over one invoice.

    Records the policy pack version so the decision can be replayed against the
    exact rules that produced it.
    """

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.invoice_id", ondelete="CASCADE"), nullable=False, index=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)

    # Cost attribution per run (FR-12.5). A touchless run should show zero
    # model tokens — that is the mechanism that makes the design scale.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0), nullable=False)
    model_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    context_utilisation: Mapped[float | None] = mapped_column(Float)

    invoice: Mapped[Invoice] = relationship(back_populates="runs")
    check_results: Mapped[list[CheckResultRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    decision: Mapped[DecisionRow | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )
    errors: Mapped[list[RunError]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    evaluation: Mapped[RunEvaluation | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class CheckResultRow(Base):
    """One rule evaluation. Rows, not a blob, so KPIs are SQL queries."""

    __tablename__ = "check_results"
    __table_args__ = (
        Index("ix_check_results_lookup", "run_id", "sequence"),
        Index("ix_check_results_analysis", "tenant_id", "name", "verdict"),
        CheckConstraint(
            "verdict IN ('pass','flag','fail','skip')",
            name="ck_check_results_verdict",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Evaluation order — also UI render order and reviewer reasoning order.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    # Kept as text so any comparable type renders faithfully without a lossy
    # numeric coercion.
    threshold: Mapped[str | None] = mapped_column(String(128))
    actual: Mapped[str | None] = mapped_column(String(128))
    inputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    forces_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    citations: Mapped[list[str] | None] = mapped_column(JSONB)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[Run] = relationship(back_populates="check_results")


class DecisionRow(Base, TimestampMixin):
    """Terminal decision for a run: what, why, and on whose authority."""

    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(
            "route IN ('auto_approve','hold','route_for_approval','reject')",
            name="ck_decisions_route",
        ),
        CheckConstraint(
            "decided_by IN ('agent','human','policy_engine')",
            name="ck_decisions_actor",
        ),
        # Segregation of duties at the storage boundary (FR-4.11): the agent can
        # never be recorded as approver. Enforced in the domain model too — a
        # deliberate second layer, because this row is the audit evidence.
        CheckConstraint(
            "NOT (decided_by = 'agent' AND approver_identity IS NOT NULL)",
            name="ck_decisions_sod_agent_not_approver",
        ),
        CheckConstraint(
            "NOT (route = 'auto_approve' AND decided_by = 'human')",
            name="ck_decisions_auto_approve_not_human",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    route: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    decided_by: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    gl_account: Mapped[str | None] = mapped_column(String(32))
    cost_center: Mapped[str | None] = mapped_column(String(64))
    entity: Mapped[str | None] = mapped_column(String(64))
    project: Mapped[str | None] = mapped_column(String(64))
    gl_confidence: Mapped[float | None] = mapped_column(Float)
    gl_inherited_from_po: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    required_approver_tier: Mapped[str | None] = mapped_column(String(64))
    approver_identity: Mapped[str | None] = mapped_column(String(255))
    citations: Mapped[list[str] | None] = mapped_column(JSONB)

    # Payment terms outcome (FR-4.9) — drives the discount-capture KPI.
    computed_due_date: Mapped[date | None] = mapped_column(Date)
    discount_deadline: Mapped[date | None] = mapped_column(Date)
    discount_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    discount_annualised_pct: Mapped[Decimal | None] = mapped_column(PERCENT)

    run: Mapped[Run] = relationship(back_populates="decision")


class RunError(Base):
    """A structured failure.

    Persisted because the fail-loud policy is only useful if failures are
    inspectable after the fact (FR-8.4).
    """

    __tablename__ = "run_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    error_class: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[Run] = relationship(back_populates="errors")


# -------------------------------------------------------------- live evals


class RunEvaluation(Base, TimestampMixin):
    """Real DeepEval scores produced asynchronously for one live document run."""

    __tablename__ = "run_evaluations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_run_evaluations_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Focused product metrics. Tool-use is the conservative minimum of DeepEval
    # tool-selection and argument-correctness scores; details retains both.
    task_completion: Mapped[float | None] = mapped_column(Float)
    tool_use: Mapped[float | None] = mapped_column(Float)
    rag_grounding: Mapped[float | None] = mapped_column(Float)
    extraction_accuracy: Mapped[float | None] = mapped_column(Float)
    policy_adherence: Mapped[float | None] = mapped_column(Float)

    judge_model: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="evaluation")


# ---------------------------------------------------------------- HITL reviews


class HitlReview(Base, TimestampMixin):
    """A human review request and its outcome.

    Escalation reaches the reviewer in their own tools (Slack/email), but is
    tracked here as the auditable system of record (FR-11.3).
    """

    __tablename__ = "hitl_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','edited','rejected','expired')",
            name="ck_hitl_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    required_tier: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)

    # LangGraph thread + interrupt identifiers, so the paused run can be resumed
    # exactly where it stopped.
    thread_id: Mapped[str | None] = mapped_column(String(128), index=True)
    interrupt_id: Mapped[str | None] = mapped_column(String(128))

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# ---------------------------------------------------------------- ERP actions


class ErpAction(Base, TimestampMixin):
    """Outbound action log with an idempotency key.

    The key exists because a retried post must not create a second bill (NFR-7).
    """

    __tablename__ = "erp_actions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_erp_action_idem"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    action: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    erp_object_id: Mapped[str | None] = mapped_column(String(64))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------- RAG corpus


class PolicyChunk(Base, TimestampMixin):
    """Retrieval corpus for GL coding (FR-6.1).

    Carries a dense `embedding` and a lexical `content_tsv` in the same row.
    Hybrid retrieval therefore needs no second datastore and no cross-system
    consistency handling (ADR-008), and tenant isolation is a SQL predicate
    rather than a library feature (FR-6.7).
    """

    __tablename__ = "policy_chunks"
    __table_args__ = (
        Index(
            "ix_policy_chunks_scope",
            "tenant_id",
            "doc_type",
            "version",
            "effective_from",
            "effective_to",
        ),
        Index(
            "ix_policy_chunks_tsv",
            "content_tsv",
            postgresql_using="gin",
        ),
        # HNSW for approximate nearest neighbour on cosine distance.
        Index(
            "ix_policy_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        CheckConstraint(
            "doc_type IN ('accounting_policy','vendor_contract','coding_precedent')",
            name="ck_policy_chunks_doc_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str] = mapped_column(String(512), nullable=False)
    section: Mapped[str | None] = mapped_column(String(255))
    # Stable citation handle, e.g. "accounting_policy.md#4.2". Required by the
    # no-ungrounded-coding rule (FR-5.3).
    citation_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    # Null = currently in force. The metadata pre-filter uses this to guarantee
    # superseded policy can never be cited.
    effective_to: Mapped[date | None] = mapped_column(Date)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    token_count: Mapped[int | None] = mapped_column(Integer)
    chunk_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# ------------------------------------------------------------------ audit log


class AuditLog(Base):
    """Append-only trail across everything.

    No update or delete path exists in application code. Corrections are new
    entries, which is what makes the trail evidence rather than a summary.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_lookup", "tenant_id", "entity_type", "entity_id"),
        Index("ix_audit_log_time", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
