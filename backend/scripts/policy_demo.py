"""Demo: run the adversarial dataset through the policy engine alone — no
model, no LLM — and print the resulting check ledger.

    uv run python scripts/policy_demo.py clean_touchless                 # one case
    uv run python scripts/policy_demo.py missing_grn --index 3
    uv run python scripts/policy_demo.py --all                          # one per mode

This is the Slice 6 demo checkpoint. Ground truth from the adversarial
fixture builders (`apfixtures.cases`) stands in for a perfect extraction — the
point here is to exercise the deterministic engine in isolation, not to
re-run extraction. Slice 13's DeepEval harness is where the full pipeline,
extraction included, is graded end to end.

Every case's `expectation.checks` in `apfixtures.cases` was authored against
these exact check names and verdicts; a mismatch printed here against a
case's stated expectation is either a real engine bug or a fixture that has
drifted from the engine's current behaviour — both worth looking at.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from ap_agent.core.canonical import (  # noqa: E402
    BankDetails,
    GoodsReceipt,
    Invoice,
    InvoiceLine,
    LineItem,
    PurchaseOrder,
    Vendor,
    VendorStatus,
)
from ap_agent.core.policy_pack import PolicyPack, load_all_policy_packs  # noqa: E402
from ap_agent.core.primitives import Extracted, Money  # noqa: E402
from ap_agent.policy import PolicyEvaluationContext, evaluate_all  # noqa: E402
from ap_agent.policy.context import HistoricalBill  # noqa: E402
from apfixtures.cases import build_case  # noqa: E402
from apfixtures.spec import (  # noqa: E402
    SEED_POS,
    VENDOR_BANK_LAST4,
    VENDOR_NAMES,
    FailureMode,
    InvoiceContent,
)

POLICY_PACK_DIR = REPO_ROOT / "backend" / "policy_packs"

# Vendor status/creation metadata mirroring `mock_erp.app.seed`, kept local so
# this demo does not depend on importing a separate deployable's internals —
# the same independence `apfixtures` itself deliberately maintains.
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

# Historical bills duplicate detection compares against, mirroring
# `apfixtures.spec.HISTORICAL_EXACT_DUPLICATE` / `HISTORICAL_FUZZY_DUPLICATE`.
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


def _to_canonical_invoice(content: InvoiceContent, invoice_id: str, tenant_id: str) -> Invoice:
    """Ground truth from the fixture as if it had been perfectly extracted."""
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


def _purchase_order_for(po_reference: str | None) -> PurchaseOrder | None:
    if po_reference is None:
        return None
    seed = SEED_POS.get(po_reference)
    if seed is None:
        return None  # malformed / unknown reference — the engine reports this
    line = LineItem(
        description=seed.line_description,
        quantity=seed.ordered_qty,
        unit_price=Money(amount=seed.unit_price, currency=seed.currency),
        line_total=Money(amount=seed.ordered_qty * seed.unit_price, currency=seed.currency),
    )
    return PurchaseOrder(
        po_number=seed.doc_number,
        vendor_id=seed.vendor_id,
        currency=seed.currency,
        order_date=date(2026, 2, 2),
        total_amount=line.line_total,
        lines=(line,),
        approved_by="buyer@example.com",
    )


def _goods_receipt_for(po_reference: str | None) -> GoodsReceipt | None:
    if po_reference is None:
        return None
    seed = SEED_POS.get(po_reference)
    if seed is None or not seed.has_grn or seed.received_qty is None:
        return None
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


def run_case(mode: FailureMode, index: int, packs: dict[str, PolicyPack]) -> None:
    case = build_case(mode, index)
    pack = packs[case.tenant_id]
    invoice = _to_canonical_invoice(case.content, case.case_id, case.tenant_id)

    context = PolicyEvaluationContext(
        invoice=invoice,
        policy=pack,
        purchase_order=_purchase_order_for(case.content.po_reference),
        goods_receipt=_goods_receipt_for(case.content.po_reference),
        vendor=_vendor_for(case.content.vendor_id),
        historical_bills=_HISTORICAL_BILLS,
    )

    ledger = evaluate_all(context)

    print("=" * 90)
    print(f"  {case.case_id}  [{case.tenant_id}]")
    print(f"  expected route: {case.expectation.route.value}")
    print("=" * 90)
    print(ledger.render())
    print()
    print(f"  clean ledger (no auto-approval block): {ledger.is_clean}")

    expected = {c.name: c.verdict.value for c in case.expectation.checks}
    mismatches = [
        (name, expected_verdict, actual.verdict.value)
        for name, expected_verdict in expected.items()
        if (actual := ledger.by_name(name)) is not None and actual.verdict.value != expected_verdict
    ]
    if mismatches:
        print("  --- MISMATCHES vs fixture expectation ---")
        for name, exp, act in mismatches:
            print(f"    {name}: expected {exp}, engine said {act}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", default=FailureMode.CLEAN_TOUCHLESS.value)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--all", action="store_true", help="One case per failure mode.")
    args = parser.parse_args(argv)

    packs = load_all_policy_packs(POLICY_PACK_DIR)

    if args.all:
        for mode in FailureMode:
            run_case(mode, 0, packs)
        return 0

    try:
        mode = FailureMode(args.mode)
    except ValueError:
        print(f"Unknown failure mode: {args.mode!r}", file=sys.stderr)
        print(f"Valid modes: {', '.join(m.value for m in FailureMode)}", file=sys.stderr)
        return 1

    run_case(mode, args.index, packs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
