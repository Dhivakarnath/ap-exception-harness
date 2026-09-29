"""Case specification types for the adversarial invoice dataset.

This dataset is the highest-leverage artefact in the project. Every accuracy
number the system reports is only meaningful because the inputs are genuinely
hard: if the generator produced clean, well-behaved invoices, a 99% extraction
score would prove nothing and any reviewer would see through it immediately.

Three properties are deliberate:

**Every case is labelled with its failure mode and its expected outcome.** A
fixture without a ground truth is a demo, not a test. `CaseExpectation` records
the extraction that should be recovered, the verdict each check should reach, and
the route the invoice should take.

**Generation is deterministic.** Given the same seed, byte-identical output.
Otherwise an eval regression cannot be distinguished from dataset churn.

**Counterparty references are explicit constants, not imports.** The generator
does not import the mock ERP's seed module, so the two can be reasoned about
independently — and a dedicated drift test asserts they still agree. Importing
would make drift impossible but would also couple a fixture package to a
separate deployable's internals.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------- anchors

# Must match mock_erp.seed.TODAY. Asserted by a drift test.
DATASET_TODAY = date(2026, 3, 2)
DATASET_SEED_VERSION = "1.0.0"

# Counterparties from the mock ERP seed. Asserted by a drift test.
VENDOR_ACME = "V-1001"
VENDOR_ACME_INDUSTRIES = "V-1002"
VENDOR_GLOBEX = "V-1003"
VENDOR_INITECH = "V-1004"
VENDOR_UMBRELLA_INACTIVE = "V-1005"
VENDOR_SHELL_BLOCKED = "V-1006"
VENDOR_NORTHWIND_NEW = "V-1007"

VENDOR_NAMES: dict[str, str] = {
    VENDOR_ACME: "Acme Corporation",
    VENDOR_ACME_INDUSTRIES: "Acme Industries LLC",
    VENDOR_GLOBEX: "Globex Industries",
    VENDOR_INITECH: "Initech Services",
    VENDOR_UMBRELLA_INACTIVE: "Umbrella Supplies",
    VENDOR_SHELL_BLOCKED: "Shell Holdings Ltd",
    VENDOR_NORTHWIND_NEW: "Northwind Traders",
}

# Remit-to last-four digits held on the vendor master. An invoice presenting a
# different value is a bank-detail change and must force review (FR-4.6).
VENDOR_BANK_LAST4: dict[str, str] = {
    VENDOR_ACME: "4821",
    VENDOR_ACME_INDUSTRIES: "9903",
    VENDOR_GLOBEX: "7710",
    VENDOR_INITECH: "3312",
    VENDOR_UMBRELLA_INACTIVE: "5150",
    VENDOR_SHELL_BLOCKED: "0001",
    VENDOR_NORTHWIND_NEW: "6644",
}


class DocumentKind(StrEnum):
    """Whether the document bills for money owed or credits money back.

    A credit note is a first-class AP document, not a malformed invoice: its
    amounts are legitimately negative, and treating a negative total as an
    arithmetic error would be a false exception. Modelling it explicitly is
    what lets `math_integrity` reconcile signed amounts correctly rather than
    guessing at intent (see the credit-note failure mode).
    """

    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"


class TaxLine(BaseModel):
    """One tax or statutory-charge row in the totals block.

    Real invoices frequently carry more than one — a VAT line plus an
    environmental levy, or state plus city sales tax — and a totals block that
    only ever models a single tax row cannot represent them. Each row keeps its
    own printed label so multi-jurisdiction tax is a first-class shape rather
    than a lump sum.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    amount: Decimal
    # A reverse-charge line is printed at 0.00 with a note that the buyer
    # accounts for the VAT: the invoice legitimately charges no tax, and a
    # check that expected tax > 0 would wrongly flag it (FR-4.7).
    is_reverse_charge: bool = False


class SeedPO(BaseModel):
    """A purchase order in the mock ERP, with what we need to build cases."""

    model_config = ConfigDict(frozen=True)

    doc_number: str
    vendor_id: str
    grn_doc_number: str | None
    currency: str
    line_description: str
    ordered_qty: Decimal
    received_qty: Decimal | None
    unit_price: Decimal
    gl_account: str

    @property
    def has_grn(self) -> bool:
        return self.grn_doc_number is not None

    @property
    def is_partial(self) -> bool:
        return self.received_qty is not None and self.received_qty < self.ordered_qty


# Mirrors mock_erp.seed.PURCHASE_ORDERS. Only single-line POs are represented
# here; PO-2001 has a freight line that cases add explicitly when needed.
SEED_POS: dict[str, SeedPO] = {
    "PO-2001": SeedPO(
        doc_number="PO-2001",
        vendor_id=VENDOR_ACME,
        grn_doc_number="GRN-3001",
        currency="USD",
        line_description="Steel bracket, 40mm",
        ordered_qty=Decimal("120"),
        received_qty=Decimal("120"),
        unit_price=Decimal("12.00"),
        gl_account="5000",
    ),
    "PO-2002": SeedPO(
        doc_number="PO-2002",
        vendor_id=VENDOR_ACME,
        grn_doc_number="GRN-3002",
        currency="USD",
        line_description="Aluminium sheet, 2mm",
        ordered_qty=Decimal("400"),
        received_qty=Decimal("400"),
        unit_price=Decimal("30.00"),
        gl_account="5000",
    ),
    "PO-2003": SeedPO(
        doc_number="PO-2003",
        vendor_id=VENDOR_GLOBEX,
        grn_doc_number="GRN-3003",
        currency="USD",
        line_description="Polymer pellets, 25kg sack",
        ordered_qty=Decimal("100"),
        received_qty=Decimal("60"),
        unit_price=Decimal("50.00"),
        gl_account="5000",
    ),
    "PO-2004": SeedPO(
        doc_number="PO-2004",
        vendor_id=VENDOR_GLOBEX,
        grn_doc_number=None,
        currency="USD",
        line_description="Copper wire spool",
        ordered_qty=Decimal("80"),
        received_qty=None,
        unit_price=Decimal("40.00"),
        gl_account="5000",
    ),
    "PO-2005": SeedPO(
        doc_number="PO-2005",
        vendor_id=VENDOR_ACME,
        grn_doc_number="GRN-3005",
        currency="USD",
        line_description="Bearing assembly",
        ordered_qty=Decimal("60"),
        received_qty=Decimal("50"),
        unit_price=Decimal("30.00"),
        gl_account="5000",
    ),
    "PO-2006": SeedPO(
        doc_number="PO-2006",
        vendor_id=VENDOR_NORTHWIND_NEW,
        grn_doc_number="GRN-3006",
        currency="USD",
        line_description="Fastener kit, mixed",
        ordered_qty=Decimal("330"),
        received_qty=Decimal("330"),
        unit_price=Decimal("15.00"),
        gl_account="5000",
    ),
    "PO-2007": SeedPO(
        doc_number="PO-2007",
        vendor_id=VENDOR_ACME,
        grn_doc_number="GRN-3007",
        currency="USD",
        line_description="Gasket set",
        ordered_qty=Decimal("100"),
        received_qty=Decimal("100"),
        unit_price=Decimal("20.00"),
        gl_account="5000",
    ),
    "PO-2008": SeedPO(
        doc_number="PO-2008",
        vendor_id=VENDOR_ACME,
        grn_doc_number="GRN-3008",
        currency="USD",
        line_description="Seal ring",
        ordered_qty=Decimal("200"),
        received_qty=Decimal("200"),
        unit_price=Decimal("10.00"),
        gl_account="5000",
    ),
}

# Historical bills that duplicate detection should match against.
HISTORICAL_EXACT_DUPLICATE = ("INV-77001", VENDOR_ACME, Decimal("1500.00"), date(2026, 1, 12))
HISTORICAL_FUZZY_DUPLICATE = ("INV-88001", VENDOR_GLOBEX, Decimal("2750.00"), date(2026, 2, 3))


# ---------------------------------------------------------------- failure modes


class FailureMode(StrEnum):
    """What each case is designed to exercise.

    Named for the *condition present in the document*, not for the check that
    should catch it, so a case remains correctly labelled even if we later change
    which rule handles it.
    """

    # --- baseline ---
    CLEAN_TOUCHLESS = "clean_touchless"
    CLEAN_OVER_THRESHOLD = "clean_over_threshold"

    # --- matching ---
    MISSING_GRN = "missing_grn"
    QUANTITY_OVERBILLED = "quantity_overbilled"
    PRICE_VARIANCE_WITHIN_TOLERANCE = "price_variance_within_tolerance"
    PRICE_VARIANCE_EXCEEDS_TOLERANCE = "price_variance_exceeds_tolerance"
    PARTIAL_DELIVERY = "partial_delivery"

    # --- PO reference ---
    MISSING_PO_REFERENCE = "missing_po_reference"
    MALFORMED_PO_REFERENCE = "malformed_po_reference"

    # --- arithmetic ---
    LINE_SUM_MISMATCH = "line_sum_mismatch"
    TAX_TOTAL_MISMATCH = "tax_total_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"

    # --- duplicates ---
    EXACT_DUPLICATE = "exact_duplicate"
    FUZZY_DUPLICATE = "fuzzy_duplicate"

    # --- identity and fraud ---
    VENDOR_NAME_AMBIGUOUS = "vendor_name_ambiguous"
    BANK_DETAIL_CHANGE = "bank_detail_change"
    VENDOR_BLOCKED = "vendor_blocked"
    VENDOR_INACTIVE = "vendor_inactive"
    THRESHOLD_AVOIDANCE = "threshold_avoidance"

    # --- coding ---
    NON_PO_SERVICES = "non_po_services"

    # --- document quality ---
    LABEL_VARIATION = "label_variation"
    OCR_NOISE = "ocr_noise"
    IMAGE_ONLY = "image_only"

    # --- real-world layout complexity (research-grounded; see INC-006) ---
    CREDIT_NOTE = "credit_note"
    """Negative-amount credit memo. Legitimate, and must not read as an
    arithmetic error (Accounting Seed / Stripe reverse-invoice practice)."""

    MULTI_LINE_TAX = "multi_line_tax"
    """Two or more tax/levy rows in the totals block (multi-jurisdiction)."""

    REVERSE_CHARGE_VAT = "reverse_charge_vat"
    """Cross-border B2B invoice showing 0% VAT with a reverse-charge note; the
    buyer accounts for the tax (EU VAT domestic reverse charge, S55A)."""

    MULTI_PAGE = "multi_page"
    """Line-item table long enough to span more than one page."""

    FOREIGN_CURRENCY = "foreign_currency"
    """A wholly self-consistent non-USD invoice (EUR/GBP), symbols and all —
    distinct from CURRENCY_MISMATCH, which mixes currencies within one doc."""

    LOGO_OVERLAP = "logo_overlap"
    """A vendor logo block sitting over/beside header text, the way a real
    letterhead crowds the invoice number and date."""

    HEAVY_SKEW = "heavy_skew"
    """A scan rotated several degrees — the range a real fax or phone photo
    hits, well beyond the sub-2-degree drift the OCR_NOISE cases use."""


class RenderFormat(StrEnum):
    PDF_TEXT = "pdf_text"
    """Digital PDF with a real text layer. Docling parses structure directly."""

    PDF_SCANNED = "pdf_scanned"
    """Rasterised page embedded in a PDF — no text layer, requires OCR."""

    IMAGE = "image"
    """Bare PNG. Exercises the image-only path (FR-2.6)."""


class ExpectedRoute(StrEnum):
    """Where the invoice should end up. Mirrors `core.canonical.Route`."""

    AUTO_APPROVE = "auto_approve"
    HOLD = "hold"
    ROUTE_FOR_APPROVAL = "route_for_approval"
    REJECT = "reject"


class ExpectedVerdict(StrEnum):
    """Mirrors `core.checks.Verdict`."""

    PASS = "pass"  # noqa: S105 - a verdict, not a credential
    FLAG = "flag"
    FAIL = "fail"
    SKIP = "skip"


class ExpectedCheck(BaseModel):
    """The verdict a named check should reach for this case."""

    model_config = ConfigDict(frozen=True)

    name: str
    verdict: ExpectedVerdict
    note: str | None = None


# ------------------------------------------------------------------ content


class LineContent(BaseModel):
    """A rendered invoice line, and the values extraction should recover."""

    model_config = ConfigDict(frozen=True)

    line_number: int = Field(ge=1)
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    unit_of_measure: str = "EA"


class InvoiceContent(BaseModel):
    """Everything printed on the document.

    This is simultaneously the render input and the extraction ground truth,
    which is what keeps the manifest honest: it cannot drift from the artefact
    because both derive from the same object.
    """

    model_config = ConfigDict(frozen=True)

    invoice_number: str
    invoice_date: date
    due_date: date | None
    vendor_id: str
    vendor_name_printed: str
    """As printed. May be an alias or a near-duplicate of the master name."""
    currency: str
    subtotal: Decimal
    tax_amount: Decimal | None
    """Total tax across all rows. Stays the single reconciling scalar the
    manifest and extraction ground truth compare against; when `tax_lines` is
    populated this equals their sum, so a single-tax invoice is just the
    common case of the general one."""
    total_amount: Decimal
    lines: tuple[LineContent, ...]
    po_reference: str | None = None
    payment_terms: str | None = None
    remit_to_last4: str | None = None
    remit_to_bank_name: str | None = None
    # Currency printed on individual lines, when deliberately inconsistent with
    # the header (the currency-mismatch case).
    line_currency_override: str | None = None
    notes: str | None = None

    # --- real-world-complexity fields (all optional; default to the simple
    #     single-tax invoice so every existing case is unchanged) ---
    document_kind: DocumentKind = DocumentKind.INVOICE
    tax_lines: tuple[TaxLine, ...] = ()
    """Itemised tax/levy rows. Empty means "use the scalar `tax_amount` as a
    single unlabelled tax row", preserving existing single-tax rendering."""
    currency_symbol: str | None = None
    """Printed currency glyph (e.g. '€', '£') when a document leads with a
    symbol rather than an ISO code. None keeps the ISO-code rendering."""
    has_logo: bool = False
    """Render a vendor logo block that crowds the header text."""

    @property
    def computed_line_sum(self) -> Decimal:
        return sum((line.line_total for line in self.lines), Decimal(0))

    @property
    def is_credit_note(self) -> bool:
        return self.document_kind is DocumentKind.CREDIT_NOTE

    @property
    def effective_tax_lines(self) -> tuple[TaxLine, ...]:
        """Tax rows to render: the itemised set if present, else a single row
        synthesised from the scalar `tax_amount` so both paths render alike."""
        if self.tax_lines:
            return self.tax_lines
        if self.tax_amount is not None:
            return (TaxLine(label="Tax", amount=self.tax_amount),)
        return ()

    @model_validator(mode="after")
    def _validate_tax_coherence(self) -> Self:
        # If tax rows are itemised, the scalar must equal their sum — otherwise
        # the manifest (which stores the scalar) would disagree with the
        # document (which prints the rows), and the ground truth would be
        # internally inconsistent.
        if self.tax_lines:
            rows_sum = sum((t.amount for t in self.tax_lines), Decimal(0))
            scalar = self.tax_amount if self.tax_amount is not None else Decimal(0)
            if rows_sum != scalar:
                raise ValueError(
                    f"tax_lines sum to {rows_sum} but tax_amount is {scalar}; "
                    "the manifest scalar must equal the printed rows."
                )
        return self


class CaseExpectation(BaseModel):
    """Ground truth for one case."""

    model_config = ConfigDict(frozen=True)

    route: ExpectedRoute
    checks: tuple[ExpectedCheck, ...] = ()
    requires_human_review: bool = False
    # For non-PO cases: the account the GL-coding step should propose.
    expected_gl_account: str | None = None
    # Extraction is expected to be imperfect on degraded documents. This is the
    # floor a run must clear, not a promise of perfection.
    min_extraction_confidence: float | None = None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_coherence(self) -> Self:
        if self.route is ExpectedRoute.AUTO_APPROVE and self.requires_human_review:
            raise ValueError(
                "A case cannot both auto-approve and require human review. "
                "Pick the outcome the pipeline should actually reach."
            )
        if any(c.verdict is ExpectedVerdict.FAIL for c in self.checks) and (
            self.route is ExpectedRoute.AUTO_APPROVE
        ):
            failing = [c.name for c in self.checks if c.verdict is ExpectedVerdict.FAIL]
            raise ValueError(
                f"Case expects auto-approval while these checks fail: {failing}. "
                "A failing check must block straight-through processing."
            )
        return self


class InvoiceCase(BaseModel):
    """A complete generated case: what to render, and what should happen."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    failure_mode: FailureMode
    tenant_id: str
    render_format: RenderFormat
    content: InvoiceContent
    expectation: CaseExpectation
    # Degradation parameters for scanned/noisy renders, recorded so a run can be
    # reproduced exactly.
    noise_level: float = Field(default=0.0, ge=0.0, le=1.0)
    rotation_degrees: float = 0.0
    dpi: int = Field(default=200, ge=72, le=600)

    @property
    def filename(self) -> str:
        ext = "png" if self.render_format is RenderFormat.IMAGE else "pdf"
        return f"{self.case_id}.{ext}"
