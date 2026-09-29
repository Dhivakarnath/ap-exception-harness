"""The canonical domain model.

Everything between ingress and egress operates on these types. Vendor-specific
labels are normalised into them on the way in (FR-3.1), and they are mapped to
ERP-specific field names on the way out inside the connector (FR-3.4). The
pipeline itself never sees a vendor quirk or an ERP schema.

Three-way matching needs three documents:

  * `PurchaseOrder`  — what was **ordered** (authorised before the purchase)
  * `GoodsReceipt`   — what was **received** (confirms delivery)
  * `Invoice`        — what the vendor is **billing**

An invoice with no PO reference cannot be three-way matched at all and takes the
non-PO branch, where GL coding must be determined from policy rather than
inherited from a PO (FR-5.1, FR-5.2).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ap_agent.core.primitives import Extracted, Money

# --------------------------------------------------------------------- vendor


class VendorStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"
    """Explicitly barred — e.g. under fraud investigation."""


class BankDetails(BaseModel):
    """Remit-to details.

    Any change here forces human review regardless of amount (FR-4.6): altered
    bank details on a legitimate vendor invoice is one of the most common and
    most costly invoice fraud patterns.
    """

    model_config = ConfigDict(frozen=True)

    account_name: str | None = None
    account_number_last4: str | None = Field(default=None, max_length=4)
    routing_code: str | None = None
    iban_last4: str | None = Field(default=None, max_length=4)
    bank_name: str | None = None

    def fingerprint(self) -> str:
        """Stable identity for change detection.

        Only non-sensitive fragments are stored and compared — enough to detect
        a change, not enough to be worth stealing.
        """
        parts = [
            (self.account_name or "").strip().casefold(),
            self.account_number_last4 or "",
            (self.routing_code or "").strip().casefold(),
            self.iban_last4 or "",
            (self.bank_name or "").strip().casefold(),
        ]
        return "|".join(parts)


class Vendor(BaseModel):
    """A supplier, after entity resolution (FR-3.3)."""

    model_config = ConfigDict(frozen=True)

    vendor_id: str
    legal_name: str
    display_name: str | None = None
    status: VendorStatus = VendorStatus.ACTIVE
    tax_id: str | None = None
    default_currency: str | None = None
    default_payment_terms: str | None = None
    bank_details: BankDetails | None = None
    # Set when this vendor was created recently: a first-time or barely-known
    # vendor is itself a fraud signal at intake.
    first_seen_at: datetime | None = None

    @property
    def name(self) -> str:
        return self.display_name or self.legal_name

    @property
    def is_payable(self) -> bool:
        return self.status is VendorStatus.ACTIVE


# ----------------------------------------------------------------- line items


class LineItem(BaseModel):
    """A single billed or ordered line.

    `quantity` is Decimal, not int: real invoices bill fractional units (hours,
    kilograms, metres).
    """

    model_config = ConfigDict(frozen=True)

    description: str
    quantity: Decimal
    unit_price: Money
    line_total: Money
    sku: str | None = None
    unit_of_measure: str | None = None
    # Present on PO-backed lines; absent on non-PO invoices (FR-5.1).
    gl_account: str | None = None
    cost_center: str | None = None

    @model_validator(mode="after")
    def _validate_currency_consistency(self) -> Self:
        if self.unit_price.currency != self.line_total.currency:
            raise ValueError(
                f"Line {self.description!r} mixes currencies: "
                f"unit_price is {self.unit_price.currency}, "
                f"line_total is {self.line_total.currency}"
            )
        return self

    @property
    def expected_line_total(self) -> Money:
        """quantity x unit_price, for the arithmetic-integrity check (FR-4.7).

        Not enforced here: a mismatch is a finding to report with threshold and
        actual, not an exception that stops the document being read.
        """
        return self.unit_price * self.quantity


# ------------------------------------------------------- purchase order / GRN


class PurchaseOrder(BaseModel):
    """What was ordered, and therefore what was authorised.

    A PO-backed invoice inherits GL coding, cost center, and approval path from
    here, which is why a clean three-way match can be approved without fresh
    human authorisation: a human already authorised this PO (FR-11.4).
    """

    model_config = ConfigDict(frozen=True)

    po_number: str
    vendor_id: str
    currency: str
    order_date: date
    total_amount: Money
    lines: tuple[LineItem, ...] = ()
    status: str | None = None
    # Coding and approval attributes the invoice inherits.
    gl_account: str | None = None
    cost_center: str | None = None
    department: str | None = None
    entity: str | None = None
    project: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None

    @property
    def is_approved(self) -> bool:
        return self.approved_by is not None


class GoodsReceipt(BaseModel):
    """What was actually received. The third leg of the match."""

    model_config = ConfigDict(frozen=True)

    grn_number: str
    po_number: str
    received_date: date
    lines: tuple[LineItem, ...] = ()
    received_by: str | None = None
    is_partial: bool = False
    """Partial deliveries are normal and must not be treated as a mismatch —
    tolerance rules handle them (FR-4.2)."""


# -------------------------------------------------------------------- invoice


class InvoiceSource(StrEnum):
    """How the document arrived. v1 implements UPLOAD; the rest are v2."""

    UPLOAD = "upload"
    EMAIL = "email"
    ERP_WEBHOOK = "erp_webhook"
    BUCKET = "bucket"


class InvoiceLine(BaseModel):
    """An invoice line, carrying provenance for each extracted component."""

    model_config = ConfigDict(frozen=True)

    line_number: int = Field(ge=1)
    description: Extracted[str]
    quantity: Extracted[Decimal]
    unit_price: Extracted[Money]
    line_total: Extracted[Money]
    sku: Extracted[str] | None = None
    unit_of_measure: Extracted[str] | None = None

    def to_line_item(self) -> LineItem:
        """Strip provenance for arithmetic. Confidence is consulted before this."""
        return LineItem(
            description=self.description.value,
            quantity=self.quantity.value,
            unit_price=self.unit_price.value,
            line_total=self.line_total.value,
            sku=self.sku.value if self.sku else None,
            unit_of_measure=self.unit_of_measure.value if self.unit_of_measure else None,
        )

    @property
    def min_confidence(self) -> float:
        scores = [
            self.description.confidence,
            self.quantity.confidence,
            self.unit_price.confidence,
            self.line_total.confidence,
        ]
        return min(scores)

    def to_provenance(self) -> dict[str, object]:
        """A JSON-ready view of this line for the transparency surfaces.

        One shape used by both the live extraction event and the persisted
        `field_provenance`, so a reloaded run replays line items identically to
        the live run. Values are stringified (money carries its currency); each
        carries its own confidence, and the line's source region/bbox comes from
        the description (the anchor the extractor resolves a line's region from),
        so a click can highlight the row on the page.
        """
        region = self.description.region
        bbox = getattr(region, "bbox", None)
        return {
            "line_number": self.line_number,
            "description": str(self.description.value),
            "quantity": str(self.quantity.value),
            "unit_price": str(self.unit_price.value),
            "line_total": str(self.line_total.value),
            "unit_of_measure": (
                str(self.unit_of_measure.value) if self.unit_of_measure else None
            ),
            "sku": str(self.sku.value) if self.sku else None,
            "confidence": self.min_confidence,
            "region_ref": getattr(region, "element_ref", None),
            "page": getattr(region, "page", None),
            "bbox": (
                {
                    "left": bbox.left,
                    "top": bbox.top,
                    "right": bbox.right,
                    "bottom": bbox.bottom,
                }
                if bbox is not None
                else None
            ),
        }


class Invoice(BaseModel):
    """A vendor invoice in canonical form.

    Fields are `Extracted[...]` because every value came from a document and its
    confidence and origin matter downstream. `po_reference` being `None` is a
    meaningful business state (non-PO invoice), not missing data.
    """

    model_config = ConfigDict(frozen=True)

    # --- identity ---
    invoice_id: str
    tenant_id: str
    document_id: str
    source: InvoiceSource = InvoiceSource.UPLOAD

    # --- extracted header ---
    invoice_number: Extracted[str]
    invoice_date: Extracted[date]
    vendor_name: Extracted[str]
    currency: Extracted[str]
    subtotal: Extracted[Money]
    total_amount: Extracted[Money]
    tax_amount: Extracted[Money] | None = None
    due_date: Extracted[date] | None = None
    payment_terms: Extracted[str] | None = None
    po_reference: Extracted[str] | None = None
    remit_to: Extracted[BankDetails] | None = None

    # --- lines ---
    lines: tuple[InvoiceLine, ...] = ()

    # --- resolution results (populated by the mapping layer) ---
    resolved_vendor_id: str | None = None

    received_at: datetime | None = None

    # ------------------------------------------------------------- properties
    @property
    def is_non_po(self) -> bool:
        """True when three-way matching cannot apply (FR-5.1)."""
        return self.po_reference is None or not str(self.po_reference.value).strip()

    @property
    def is_credit_note(self) -> bool:
        """True when this document credits money back rather than billing for it.

        Detected by a negative total: a credit note (vendor credit memo) mirrors
        an invoice with negative amounts. It is a legitimate AP document, not a
        malformed invoice — but forward three-way matching against a
        positive-quantity PO does not apply to a reversal, so the matching check
        treats it like a non-PO invoice and skips rather than pretending a clean
        match. A dedicated `document_kind` on the extracted model would be a
        stronger signal than the sign; using the sign keeps this a pure property
        of amounts already present, and a negative total with no crediting
        intent is itself a defect worth surfacing.
        """
        return self.total_amount.value.amount < 0

    @property
    def header_confidence(self) -> float:
        """Lowest confidence among fields that drive money decisions.

        Deliberately a minimum, not a mean: a single badly-read total is not
        redeemed by ten confidently-read incidental fields.
        """
        scores = [
            self.invoice_number.confidence,
            self.invoice_date.confidence,
            self.vendor_name.confidence,
            self.currency.confidence,
            self.subtotal.confidence,
            self.total_amount.confidence,
        ]
        if self.tax_amount is not None:
            scores.append(self.tax_amount.confidence)
        if self.po_reference is not None:
            scores.append(self.po_reference.confidence)
        return min(scores)

    @property
    def overall_confidence(self) -> float:
        line_scores = [line.min_confidence for line in self.lines]
        return min([self.header_confidence, *line_scores]) if line_scores else self.header_confidence

    @property
    def line_items(self) -> tuple[LineItem, ...]:
        return tuple(line.to_line_item() for line in self.lines)

    def computed_line_sum(self) -> Money | None:
        """Sum of line totals, for the arithmetic check (FR-4.7).

        Returns None when there are no lines (header-only invoices are common
        for services). Raises on mixed currencies, which is a genuine data
        defect the currency-consistency check reports separately.
        """
        items = self.line_items
        if not items:
            return None
        total = Money.zero(items[0].line_total.currency)
        for item in items:
            total = total + item.line_total
        return total

    def currencies_present(self) -> set[str]:
        """All currency codes appearing anywhere. More than one is a defect."""
        codes = {
            self.currency.value,
            self.subtotal.value.currency,
            self.total_amount.value.currency,
        }
        if self.tax_amount is not None:
            codes.add(self.tax_amount.value.currency)
        for line in self.lines:
            codes.add(line.unit_price.value.currency)
            codes.add(line.line_total.value.currency)
        return codes


# ------------------------------------------------------------------- decision


class Route(StrEnum):
    """Terminal routing outcome for an invoice."""

    AUTO_APPROVE = "auto_approve"
    """Clean match under the touchless threshold. Posted without human review."""

    HOLD = "hold"
    """Blocked pending correction — e.g. a failed match a human must resolve."""

    ROUTE_FOR_APPROVAL = "route_for_approval"
    """Requires a human approver per the DOA matrix or a review-forcing check."""

    REJECT = "reject"
    """Definitively not payable — e.g. confirmed duplicate, blocked vendor."""


class Actor(StrEnum):
    """Who made a decision. Central to segregation of duties (FR-4.11)."""

    AGENT = "agent"
    HUMAN = "human"
    POLICY_ENGINE = "policy_engine"
    """Deterministic rules, distinguished from the probabilistic agent."""


class GLCoding(BaseModel):
    """Proposed accounting treatment.

    `citations` is not optional in practice: uncited coding cannot be
    auto-applied (FR-5.4). Enforced by the faithfulness gate, and asserted here
    via `is_groundable`.
    """

    model_config = ConfigDict(frozen=True)

    gl_account: str
    cost_center: str | None = None
    entity: str | None = None
    project: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    citations: tuple[str, ...] = ()
    reasoning: str | None = None
    inherited_from_po: bool = False

    @property
    def is_groundable(self) -> bool:
        """Coding may be auto-applied only if inherited from a PO or cited."""
        return self.inherited_from_po or bool(self.citations)


class Decision(BaseModel):
    """The outcome of a run: what to do, why, and on whose authority."""

    model_config = ConfigDict(frozen=True)

    invoice_id: str
    route: Route
    decided_by: Actor
    rationale: str = Field(min_length=1)
    decided_at: datetime
    gl_coding: GLCoding | None = None
    required_approver_tier: str | None = None
    approver_identity: str | None = None
    citations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_authority(self) -> Self:
        # Segregation of duties (FR-4.11): the agent may code and route, but the
        # agent is never the approver of record for a human-approval route.
        if (
            self.route is Route.ROUTE_FOR_APPROVAL
            and self.decided_by is Actor.AGENT
            and self.approver_identity is not None
        ):
            raise ValueError(
                "Segregation of duties violation: the agent cannot record "
                "itself as approver for a route requiring human approval."
            )
        if self.route is Route.AUTO_APPROVE and self.decided_by is Actor.HUMAN:
            raise ValueError(
                "AUTO_APPROVE means no human was involved. A human decision "
                "should be recorded as ROUTE_FOR_APPROVAL with an approver."
            )
        return self

    @property
    def is_touchless(self) -> bool:
        return self.route is Route.AUTO_APPROVE
