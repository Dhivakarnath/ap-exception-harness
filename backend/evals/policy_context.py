"""Shared policy-evaluation context assembly for the eval harness."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

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
from ap_agent.core.policy_pack import PolicyPack, load_all_policy_packs
from ap_agent.core.primitives import Extracted, Money
from ap_agent.mapping.vendors import ResolutionOutcome, VendorCandidate, VendorResolution
from ap_agent.policy.context import HistoricalBill, PolicyEvaluationContext
from apfixtures.cases import build_case
from apfixtures.manifest import ManifestEntry
from apfixtures.spec import (
    SEED_POS,
    VENDOR_ACME,
    VENDOR_ACME_INDUSTRIES,
    VENDOR_BANK_LAST4,
    VENDOR_NAMES,
    FailureMode,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PACK_DIR = REPO_ROOT / "backend" / "policy_packs"

_VENDOR_STATUS: dict[str, VendorStatus] = {
    "V-1005": VendorStatus.INACTIVE,
    "V-1006": VendorStatus.BLOCKED,
}
_VENDOR_FIRST_SEEN: dict[str, datetime] = {
    "V-1001": datetime(2021, 4, 12, tzinfo=UTC),
    "V-1002": datetime(2022, 9, 30, tzinfo=UTC),
    "V-1003": datetime(2020, 1, 20, tzinfo=UTC),
    "V-1004": datetime(2023, 6, 5, tzinfo=UTC),
    "V-1005": datetime(2019, 11, 2, tzinfo=UTC),
    "V-1006": datetime(2026, 1, 8, tzinfo=UTC),
    "V-1007": datetime(2026, 2, 24, tzinfo=UTC),
}
_HISTORICAL_BILLS: tuple[HistoricalBill, ...] = (
    HistoricalBill(
        invoice_number="INV-77001",
        vendor_id="V-1001",
        amount=Money(amount=Decimal("1500.00"), currency="USD"),
        invoice_date=date(2026, 1, 12),
    ),
    HistoricalBill(
        invoice_number="INV-88001",
        vendor_id="V-1003",
        amount=Money(amount=Decimal("2750.00"), currency="USD"),
        invoice_date=date(2026, 2, 3),
    ),
)


def _extracted(value: object) -> Extracted:  # type: ignore[type-arg]
    return Extracted(value=value, confidence=1.0)


def _parse_case_index(case_id: str) -> tuple[FailureMode, int]:
    stem, _, idx = case_id.rpartition("-")
    mode = FailureMode(stem)
    return mode, int(idx)


def build_policy_context_for_entry(entry: ManifestEntry) -> PolicyEvaluationContext:
    mode, index = _parse_case_index(entry.case_id)
    case = build_case(mode, index)
    packs = load_all_policy_packs(POLICY_PACK_DIR)
    pack = packs[case.tenant_id]
    invoice = _invoice_from_case(case.content, case.case_id, case.tenant_id)

    vendor = _vendor_for(case.content.vendor_id)
    vendor_resolution = None
    if mode is FailureMode.VENDOR_NAME_AMBIGUOUS:
        printed = case.content.vendor_name_printed
        invoice = invoice.model_copy(update={"resolved_vendor_id": None})
        vendor = None
        vendor_resolution = VendorResolution(
            printed_name=printed,
            outcome=ResolutionOutcome.AMBIGUOUS,
            candidates=(
                VendorCandidate(
                    vendor_id=VENDOR_ACME,
                    legal_name=VENDOR_NAMES[VENDOR_ACME],
                    score=0.85,
                ),
                VendorCandidate(
                    vendor_id=VENDOR_ACME_INDUSTRIES,
                    legal_name=VENDOR_NAMES[VENDOR_ACME_INDUSTRIES],
                    score=0.82,
                ),
            ),
            reasoning=f"{printed!r} matches multiple vendors.",
        )

    return PolicyEvaluationContext(
        invoice=invoice,
        policy=pack,
        purchase_order=_purchase_order_for(
            case.content.po_reference, mode=mode, invoice=invoice
        ),
        goods_receipt=_goods_receipt_for(
            case.content.po_reference, mode=mode, invoice=invoice
        ),
        vendor=vendor,
        vendor_resolution=vendor_resolution,
        historical_bills=_HISTORICAL_BILLS,
        coder_identity="agent@ap-system",
        approver_identity="controller@corp",
        payer_identity="treasury@corp",
    )


def load_tenant_policy(tenant_id: str) -> PolicyPack:
    return load_all_policy_packs(POLICY_PACK_DIR)[tenant_id]


def _invoice_from_case(content: object, invoice_id: str, tenant_id: str) -> Invoice:
    from apfixtures.spec import InvoiceContent

    if not isinstance(content, InvoiceContent):
        msg = f"expected InvoiceContent, got {type(content).__name__}"
        raise TypeError(msg)
    lines = tuple(
        InvoiceLine(
            line_number=line.line_number,
            description=_extracted(line.description),
            quantity=_extracted(line.quantity),
            unit_price=_extracted(
                Money(amount=line.unit_price, currency=content.line_currency_override or content.currency)
            ),
            line_total=_extracted(
                Money(amount=line.line_total, currency=content.line_currency_override or content.currency)
            ),
        )
        for line in content.lines
    )
    remit_to = (
        _extracted(
            BankDetails(
                account_number_last4=content.remit_to_last4,
                bank_name=content.remit_to_bank_name,
            )
        )
        if content.remit_to_last4 or content.remit_to_bank_name
        else None
    )
    return Invoice(
        invoice_id=invoice_id,
        tenant_id=tenant_id,
        document_id=f"doc-{invoice_id}",
        invoice_number=_extracted(content.invoice_number),
        invoice_date=_extracted(content.invoice_date),
        vendor_name=_extracted(content.vendor_name_printed),
        currency=_extracted(content.currency),
        subtotal=_extracted(Money(amount=content.subtotal, currency=content.currency)),
        total_amount=_extracted(Money(amount=content.total_amount, currency=content.currency)),
        tax_amount=(
            _extracted(Money(amount=content.tax_amount, currency=content.currency))
            if content.tax_amount is not None
            else None
        ),
        due_date=_extracted(content.due_date) if content.due_date is not None else None,
        payment_terms=_extracted(content.payment_terms) if content.payment_terms else None,
        po_reference=_extracted(content.po_reference) if content.po_reference else None,
        remit_to=remit_to,
        lines=lines,
        resolved_vendor_id=content.vendor_id,
    )


def _line_from_invoice(
    seed: object,
    invoice: Invoice,
) -> tuple[LineItem, str]:
    """Align PO/GRN lines with the invoice for modes where seed qty/price diverge."""
    inv_line = invoice.lines[0]
    currency = str(invoice.currency.value)
    qty = inv_line.quantity.value
    unit = inv_line.unit_price.value.amount
    line = LineItem(
        description=seed.line_description,  # type: ignore[attr-defined]
        quantity=qty,
        unit_price=Money(amount=unit, currency=currency),
        line_total=Money(amount=qty * unit, currency=currency),
    )
    return line, currency


def _purchase_order_for(
    po_reference: str | None,
    *,
    mode: FailureMode | None = None,
    invoice: Invoice | None = None,
) -> PurchaseOrder | None:
    if po_reference is None:
        return None
    seed = SEED_POS.get(po_reference)
    if seed is None:
        return None
    if (
        mode
        in (
            FailureMode.THRESHOLD_AVOIDANCE,
            FailureMode.FOREIGN_CURRENCY,
            FailureMode.EXACT_DUPLICATE,
        )
        and invoice
    ):
        line, currency = _line_from_invoice(seed, invoice)
    else:
        currency = seed.currency
        line = LineItem(
            description=seed.line_description,
            quantity=seed.ordered_qty,
            unit_price=Money(amount=seed.unit_price, currency=currency),
            line_total=Money(amount=seed.ordered_qty * seed.unit_price, currency=currency),
        )
    return PurchaseOrder(
        po_number=seed.doc_number,
        vendor_id=seed.vendor_id,
        currency=currency,
        order_date=date(2026, 2, 2),
        total_amount=line.line_total,
        lines=(line,),
        approved_by="buyer@example.com",
    )


def _goods_receipt_for(
    po_reference: str | None,
    *,
    mode: FailureMode | None = None,
    invoice: Invoice | None = None,
) -> GoodsReceipt | None:
    if po_reference is None:
        return None
    seed = SEED_POS.get(po_reference)
    if seed is None or not seed.has_grn or seed.received_qty is None:
        return None
    if (
        mode
        in (
            FailureMode.THRESHOLD_AVOIDANCE,
            FailureMode.FOREIGN_CURRENCY,
            FailureMode.EXACT_DUPLICATE,
        )
        and invoice
    ):
        line, _currency = _line_from_invoice(seed, invoice)
    else:
        line = LineItem(
            description=seed.line_description,
            quantity=seed.received_qty,
            unit_price=Money(amount=seed.unit_price, currency=seed.currency),
            line_total=Money(amount=seed.received_qty * seed.unit_price, currency=seed.currency),
        )
    return GoodsReceipt(
        grn_number=seed.grn_doc_number or "GRN-UNKNOWN",
        po_number=seed.doc_number,
        received_date=date(2026, 2, 10),
        lines=(line,),
        is_partial=seed.is_partial,
    )


def _vendor_for(vendor_id: str) -> Vendor:
    return Vendor(
        vendor_id=vendor_id,
        legal_name=VENDOR_NAMES.get(vendor_id, vendor_id),
        status=_VENDOR_STATUS.get(vendor_id, VendorStatus.ACTIVE),
        bank_details=BankDetails(account_number_last4=VENDOR_BANK_LAST4.get(vendor_id)),
        first_seen_at=_VENDOR_FIRST_SEEN.get(vendor_id),
    )
