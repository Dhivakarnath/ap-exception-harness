"""What a rule evaluator is handed.

`PolicyEvaluationContext` bundles an `Invoice` with its `PolicyPack` and every
already-resolved counterpart record a rule might need: the `PurchaseOrder` and
`GoodsReceipt` it cites, its resolved `Vendor`, the mapping layer's
`VendorResolution` (which may be ambiguous or unresolved — that is itself a
signal, not an error), and the historical bills duplicate detection compares
against.

**No field on this context is fetched by the engine itself.** Loading a PO
from the ERP, resolving a vendor name, pulling paid-bill history — all of that
is the connector and mapping layer's job, built separately. This context is
the seam: everything a rule needs arrives already loaded, so every rule stays
a pure function of its arguments.

**Deliberately no "current date" field.** Every date-relative computation in
this package — duplicate lookback windows, "is this vendor new" — is
evaluated relative to `invoice.invoice_date`, never wall-clock time. A rule
whose answer depended on *when it happened to run* would violate the
determinism property this package is built around: identical inputs must
always yield identical verdicts (FR-7.4), and "today" is not part of the
input, it is an ambient side channel.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from ap_agent.core.canonical import GoodsReceipt, Invoice, PurchaseOrder, Vendor
from ap_agent.core.policy_pack import PolicyPack
from ap_agent.core.primitives import Money
from ap_agent.mapping.vendors import VendorResolution


class HistoricalBill(BaseModel):
    """One previously recorded bill, for duplicate detection to compare against.

    Deliberately narrower than the full `Invoice` canonical model: duplicate
    detection needs only the fields it actually compares (FR-4.3), and a
    smaller shape means the caller assembling history from an ERP or ledger
    query does not need to reconstruct a full extraction-shaped `Invoice` for
    every past bill.
    """

    model_config = ConfigDict(frozen=True)

    invoice_number: str = Field(min_length=1)
    vendor_id: str = Field(min_length=1)
    vendor_name: str | None = None
    """Fallback comparison key for when the current invoice's vendor could not
    be resolved to an id (e.g. an ambiguous printed name). Duplicate detection
    still has a job to do in that case — a resubmitted invoice number and
    amount are suspicious independent of whether identity resolution
    succeeded — so it falls back to comparing printed names."""
    amount: Money
    invoice_date: date
    status: str | None = None


class PolicyEvaluationContext(BaseModel):
    """Everything one rule evaluation needs, already loaded."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    invoice: Invoice
    policy: PolicyPack

    purchase_order: PurchaseOrder | None = None
    goods_receipt: GoodsReceipt | None = None

    vendor: Vendor | None = None
    """The single resolved vendor, when resolution succeeded unambiguously.
    None both when resolution has not run and when it concluded AMBIGUOUS or
    NOT_FOUND — `vendor_resolution` is the check that distinguishes those."""
    vendor_resolution: VendorResolution | None = None

    historical_bills: tuple[HistoricalBill, ...] = ()
    """Candidate prior bills for duplicate detection. Expected to already be
    scoped to the tenant; this context does not re-check tenant isolation."""

    # Optional actor identities for `sod_check`. All default to None because
    # this engine slice runs ahead of the supervisor that will eventually
    # supply them (Slice 8) — `sod_check` still has a meaningful, structural
    # job to do without them (see `policy.authority`).
    coder_identity: str | None = None
    approver_identity: str | None = None
    payer_identity: str | None = None

    @property
    def resolved_vendor_id(self) -> str | None:
        """The vendor id to key identity-dependent checks on, if any.

        Prefers the directly-supplied `vendor` (the caller's strongest
        signal) and falls back to whatever the invoice itself carries from an
        earlier mapping pass.
        """
        if self.vendor is not None:
            return self.vendor.vendor_id
        return self.invoice.resolved_vendor_id

    @property
    def vendor_identity_key(self) -> tuple[str, str] | None:
        """A comparison key for duplicate detection: a resolved id when one
        exists, otherwise the printed vendor name, normalised.

        Returning a tagged tuple rather than a bare string keeps the two cases
        from ever being compared to each other by accident — an id and a name
        must never be treated as the same kind of key.
        """
        vendor_id = self.resolved_vendor_id
        if vendor_id is not None:
            return ("id", vendor_id)
        name = self.invoice.vendor_name.value.strip()
        if not name:
            return None
        return ("name", name.casefold())
