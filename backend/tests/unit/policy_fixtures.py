"""Shared builders for policy-engine unit tests.

Not a `conftest.py` module: these are plain builder functions used across
several `test_policy_*.py` files, not pytest fixtures with request-scoped
lifecycles — every test wants a *slightly* different invoice/PO/context, so
keyword-argument builders are the better fit than fixture parametrisation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ap_agent.core.canonical import (
    BankDetails,
    GoodsReceipt,
    Invoice,
    InvoiceLine,
    LineItem,
    PurchaseOrder,
    Vendor,
    VendorStatus,
)
from ap_agent.core.policy_pack import (
    DOABand,
    DOAMatrix,
    DuplicateRules,
    PaymentTermsDefaults,
    PolicyPack,
    SoDRules,
    Thresholds,
    Tolerances,
)
from ap_agent.core.primitives import Extracted, Money
from ap_agent.mapping.vendors import ResolutionOutcome, VendorResolution
from ap_agent.policy.context import HistoricalBill, PolicyEvaluationContext

TENANT = "policy-test-tenant"
VENDOR_ID = "V-TEST-1"


def money(amount: str, currency: str = "USD") -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def extracted(value: object, *, confidence: float = 1.0) -> Extracted:  # type: ignore[type-arg]
    return Extracted(value=value, confidence=confidence)


def doa_matrix(*bands: tuple[str, str | None]) -> DOAMatrix:
    return DOAMatrix(
        bands=tuple(
            DOABand(tier=tier, max_amount=Decimal(amount) if amount is not None else None)
            for tier, amount in bands
        )
    )


def policy_pack(**overrides: object) -> PolicyPack:
    base: dict[str, object] = {
        "tenant_id": TENANT,
        "version": "1.0.0",
        "industry": "test",
        "base_currency": "USD",
        "tolerances": Tolerances(
            price_pct=Decimal("2.0"),
            quantity_pct=Decimal("0.0"),
            total_absolute=Decimal("1.00"),
            allow_partial_delivery=True,
            allow_overbilling=False,
        ),
        "thresholds": Thresholds(
            touchless_max=Decimal("2500.00"),
            min_confidence_for_touchless=0.90,
            min_confidence_for_gl_coding=0.85,
            threshold_avoidance_band_pct=Decimal("5.0"),
            new_vendor_days=30,
        ),
        "doa": doa_matrix(("buyer", "5000.00"), ("controller", "25000.00"), ("cfo", None)),
        "sod": SoDRules(),
        "duplicates": DuplicateRules(
            lookback_days=90,
            fuzzy_number_similarity=0.85,
            fuzzy_amount_tolerance=Decimal("0.00"),
            fuzzy_date_window_days=7,
        ),
        "payment_terms": PaymentTermsDefaults(default_terms="NET30"),
        "require_grn": True,
        "allow_non_po_invoices": False,
    }
    base.update(overrides)
    return PolicyPack.model_validate(base)


def line_item(
    *,
    description: str = "Steel bracket, 40mm",
    quantity: str = "20",
    unit_price: str = "12.00",
    line_total: str | None = None,
    currency: str = "USD",
) -> LineItem:
    total = line_total if line_total is not None else str(Decimal(quantity) * Decimal(unit_price))
    return LineItem(
        description=description,
        quantity=Decimal(quantity),
        unit_price=money(unit_price, currency),
        line_total=money(total, currency),
    )


def invoice_line(
    *,
    line_number: int = 1,
    description: str = "Steel bracket, 40mm",
    quantity: str = "20",
    unit_price: str = "12.00",
    line_total: str | None = None,
    currency: str = "USD",
    confidence: float = 1.0,
) -> InvoiceLine:
    total = line_total if line_total is not None else str(Decimal(quantity) * Decimal(unit_price))
    return InvoiceLine(
        line_number=line_number,
        description=extracted(description, confidence=confidence),
        quantity=extracted(Decimal(quantity), confidence=confidence),
        unit_price=extracted(money(unit_price, currency), confidence=confidence),
        line_total=extracted(money(total, currency), confidence=confidence),
    )


def invoice(
    *,
    invoice_id: str = "inv-1",
    invoice_number: str = "INV-60000",
    invoice_date_: date = date(2026, 2, 27),
    vendor_name: str = "Acme Corporation",
    currency: str = "USD",
    subtotal: str = "240.00",
    total_amount: str = "259.80",
    tax_amount: str | None = "19.80",
    due_date: date | None = date(2026, 3, 29),
    payment_terms: str | None = "Net 30",
    po_reference: str | None = "PO-2001",
    remit_to_last4: str | None = "4821",
    remit_to_bank_name: str | None = None,
    lines: tuple[InvoiceLine, ...] | None = None,
    resolved_vendor_id: str | None = None,
) -> Invoice:
    default_lines = (
        invoice_line(quantity="20", unit_price="12.00", line_total=subtotal),
    )
    return Invoice(
        invoice_id=invoice_id,
        tenant_id=TENANT,
        document_id="doc-1",
        invoice_number=extracted(invoice_number),
        invoice_date=extracted(invoice_date_),
        vendor_name=extracted(vendor_name),
        currency=extracted(currency),
        subtotal=extracted(money(subtotal, currency)),
        total_amount=extracted(money(total_amount, currency)),
        tax_amount=extracted(money(tax_amount, currency)) if tax_amount is not None else None,
        due_date=extracted(due_date) if due_date is not None else None,
        payment_terms=extracted(payment_terms) if payment_terms is not None else None,
        po_reference=extracted(po_reference) if po_reference is not None else None,
        remit_to=(
            extracted(
                BankDetails(account_number_last4=remit_to_last4, bank_name=remit_to_bank_name)
            )
            if remit_to_last4 is not None or remit_to_bank_name is not None
            else None
        ),
        lines=lines if lines is not None else default_lines,
        resolved_vendor_id=resolved_vendor_id,
    )


def purchase_order(
    *,
    po_number: str = "PO-2001",
    vendor_id: str = VENDOR_ID,
    currency: str = "USD",
    order_date: date = date(2026, 2, 2),
    unit_price: str = "12.00",
    quantity: str = "120",
    approved_by: str | None = "buyer@example.com",
) -> PurchaseOrder:
    line = line_item(quantity=quantity, unit_price=unit_price, currency=currency)
    return PurchaseOrder(
        po_number=po_number,
        vendor_id=vendor_id,
        currency=currency,
        order_date=order_date,
        total_amount=money(str(line.line_total.amount), currency),
        lines=(line,),
        approved_by=approved_by,
    )


def goods_receipt(
    *,
    grn_number: str = "GRN-3001",
    po_number: str = "PO-2001",
    received_date: date = date(2026, 2, 10),
    quantity: str = "120",
    unit_price: str = "12.00",
    currency: str = "USD",
    is_partial: bool = False,
) -> GoodsReceipt:
    return GoodsReceipt(
        grn_number=grn_number,
        po_number=po_number,
        received_date=received_date,
        lines=(line_item(quantity=quantity, unit_price=unit_price, currency=currency),),
        is_partial=is_partial,
    )


def vendor(
    *,
    vendor_id: str = VENDOR_ID,
    legal_name: str = "Acme Corporation",
    status: VendorStatus = VendorStatus.ACTIVE,
    bank_last4: str | None = "4821",
    bank_name: str | None = None,
    first_seen_at: datetime | None = datetime(2021, 4, 12),
) -> Vendor:
    return Vendor(
        vendor_id=vendor_id,
        legal_name=legal_name,
        status=status,
        bank_details=(
            BankDetails(account_number_last4=bank_last4, bank_name=bank_name)
            if bank_last4 is not None or bank_name is not None
            else None
        ),
        first_seen_at=first_seen_at,
    )


def resolved(vendor_id: str = VENDOR_ID, printed_name: str = "Acme Corporation") -> VendorResolution:
    return VendorResolution(
        printed_name=printed_name,
        outcome=ResolutionOutcome.RESOLVED,
        vendor_id=vendor_id,
        reasoning=f"{printed_name!r} resolved to {vendor_id}.",
    )


def historical_bill(
    *,
    invoice_number: str = "INV-77001",
    vendor_id: str = VENDOR_ID,
    vendor_name: str | None = None,
    amount: str = "1500.00",
    currency: str = "USD",
    invoice_date_: date = date(2026, 1, 12),
    status: str | None = "paid",
) -> HistoricalBill:
    return HistoricalBill(
        invoice_number=invoice_number,
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        amount=money(amount, currency),
        invoice_date=invoice_date_,
        status=status,
    )


def context(
    *,
    inv: Invoice | None = None,
    policy: PolicyPack | None = None,
    po: PurchaseOrder | None = None,
    grn: GoodsReceipt | None = None,
    ven: Vendor | None = None,
    vendor_res: VendorResolution | None = None,
    bills: tuple[HistoricalBill, ...] = (),
    coder_identity: str | None = None,
    approver_identity: str | None = None,
    payer_identity: str | None = None,
) -> PolicyEvaluationContext:
    return PolicyEvaluationContext(
        invoice=inv if inv is not None else invoice(),
        policy=policy if policy is not None else policy_pack(),
        purchase_order=po,
        goods_receipt=grn,
        vendor=ven,
        vendor_resolution=vendor_res,
        historical_bills=bills,
        coder_identity=coder_identity,
        approver_identity=approver_identity,
        payer_identity=payer_identity,
    )
