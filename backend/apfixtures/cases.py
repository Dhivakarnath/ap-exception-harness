"""Case builders — one per failure mode.

Each builder produces a case whose *document content* embodies the failure mode
and whose *expectation* states what the pipeline should conclude. The pairing is
the whole value: it is what turns a pile of PDFs into a graded exam.

Two conventions worth stating:

**Expectations name checks, not implementations.** A case says
`three_way_match -> fail`, not "the tolerance branch on line 84 returns False".
So the dataset stays valid when the engine is refactored.

**Variation is index-derived, never random.** Amounts, dates, labels, and
degradation all derive from the case index, so `build --seed N` is reproducible
and a diff between two runs means a real change.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from apfixtures.spec import (
    DATASET_TODAY,
    HISTORICAL_EXACT_DUPLICATE,
    HISTORICAL_FUZZY_DUPLICATE,
    SEED_POS,
    VENDOR_ACME,
    VENDOR_ACME_INDUSTRIES,
    VENDOR_BANK_LAST4,
    VENDOR_INITECH,
    VENDOR_NAMES,
    VENDOR_NORTHWIND_NEW,
    VENDOR_SHELL_BLOCKED,
    VENDOR_UMBRELLA_INACTIVE,
    CaseExpectation,
    DocumentKind,
    ExpectedCheck,
    ExpectedRoute,
    ExpectedVerdict,
    FailureMode,
    InvoiceCase,
    InvoiceContent,
    LineContent,
    RenderFormat,
    SeedPO,
    TaxLine,
)

TENANT_MANUFACTURING = "manufacturing-demo"
TENANT_RETAIL = "retail-demo"

TAX_RATE = Decimal("0.0825")

_PASS = ExpectedVerdict.PASS
_FAIL = ExpectedVerdict.FAIL
_FLAG = ExpectedVerdict.FLAG
_SKIP = ExpectedVerdict.SKIP


# --------------------------------------------------------------------- helpers


def _quantise(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"))


def _tax_for(subtotal: Decimal) -> Decimal:
    return _quantise(subtotal * TAX_RATE)


def _invoice_number(prefix: str, index: int) -> str:
    return f"{prefix}-{60000 + index}"


def _dates(index: int) -> tuple[date, date]:
    """Invoice date and Net-30 due date, spread deterministically."""
    issued = DATASET_TODAY - timedelta(days=3 + (index % 21))
    return issued, issued + timedelta(days=30)


def _line(
    po: SeedPO, qty: Decimal, unit_price: Decimal, line_number: int = 1
) -> LineContent:
    return LineContent(
        line_number=line_number,
        description=po.line_description,
        quantity=qty,
        unit_price=unit_price,
        line_total=_quantise(qty * unit_price),
    )


def _content_from_po(
    po: SeedPO,
    *,
    index: int,
    qty: Decimal,
    unit_price: Decimal,
    prefix: str = "INV",
    include_tax: bool = True,
    po_reference: str | None = "__default__",
    vendor_name: str | None = None,
    remit_last4: str | None = "__default__",
    extra_lines: tuple[LineContent, ...] = (),
) -> InvoiceContent:
    """Build invoice content consistent with a seeded PO unless told otherwise."""
    issued, due = _dates(index)
    lines = (_line(po, qty, unit_price), *extra_lines)
    subtotal = _quantise(sum((line.line_total for line in lines), Decimal(0)))
    tax = _tax_for(subtotal) if include_tax else None
    total = _quantise(subtotal + (tax or Decimal(0)))

    return InvoiceContent(
        invoice_number=_invoice_number(prefix, index),
        invoice_date=issued,
        due_date=due,
        vendor_id=po.vendor_id,
        vendor_name_printed=vendor_name or VENDOR_NAMES[po.vendor_id],
        currency=po.currency,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=total,
        lines=lines,
        po_reference=(po.doc_number if po_reference == "__default__" else po_reference),
        payment_terms="Net 30",
        remit_to_last4=(
            VENDOR_BANK_LAST4[po.vendor_id] if remit_last4 == "__default__" else remit_last4
        ),
    )


def _clean_checks() -> tuple[ExpectedCheck, ...]:
    return (
        ExpectedCheck(name="completeness", verdict=_PASS),
        ExpectedCheck(name="math_integrity", verdict=_PASS),
        ExpectedCheck(name="currency_consistency", verdict=_PASS),
        ExpectedCheck(name="duplicate_exact", verdict=_PASS),
        ExpectedCheck(name="duplicate_fuzzy", verdict=_PASS),
        ExpectedCheck(name="vendor_active", verdict=_PASS),
        ExpectedCheck(name="bank_detail_change", verdict=_PASS),
        ExpectedCheck(name="three_way_match", verdict=_PASS),
        ExpectedCheck(name="threshold_avoidance", verdict=_PASS),
        ExpectedCheck(name="sod_check", verdict=_PASS),
    )


def _override(
    base: tuple[ExpectedCheck, ...], *changes: ExpectedCheck
) -> tuple[ExpectedCheck, ...]:
    """Replace named checks in a baseline expectation."""
    replaced = {c.name: c for c in changes}
    merged = [replaced.pop(c.name, c) for c in base]
    return (*merged, *replaced.values())


_MANUFACTURING_DOA_BOUNDARY = Decimal("5000.00")
_THRESHOLD_AVOIDANCE_BAND_PCT = Decimal("5.0")


def _threshold_avoidance_expectation(total: Decimal) -> ExpectedCheck:
    """Match manufacturing.yaml DOA boundary + threshold_avoidance_band_pct."""
    boundary = _MANUFACTURING_DOA_BOUNDARY
    if total > boundary:
        return ExpectedCheck(name="threshold_avoidance", verdict=_PASS)
    gap_pct = ((boundary - total) / boundary) * Decimal(100)
    if gap_pct <= _THRESHOLD_AVOIDANCE_BAND_PCT:
        return ExpectedCheck(
            name="threshold_avoidance",
            verdict=_FLAG,
            note=(
                f"Total {total} sits within {_THRESHOLD_AVOIDANCE_BAND_PCT}% of "
                f"the {boundary} DOA boundary."
            ),
        )
    return ExpectedCheck(name="threshold_avoidance", verdict=_PASS)


# ---------------------------------------------------------------------- builders
# Each takes a case index and returns a fully-specified case.

CaseBuilder = Callable[[int], InvoiceCase]


def _case(
    *,
    mode: FailureMode,
    index: int,
    content: InvoiceContent,
    expectation: CaseExpectation,
    tenant: str = TENANT_MANUFACTURING,
    render_format: RenderFormat = RenderFormat.PDF_TEXT,
    noise: float = 0.0,
    rotation: float = 0.0,
    dpi: int = 150,
) -> InvoiceCase:
    return InvoiceCase(
        case_id=f"{mode.value}-{index:03d}",
        failure_mode=mode,
        tenant_id=tenant,
        render_format=render_format,
        content=content,
        expectation=expectation,
        noise_level=noise,
        rotation_degrees=rotation,
        dpi=dpi,
    )


# --- baseline ---------------------------------------------------------------


def build_clean_touchless(index: int) -> InvoiceCase:
    """Clean three-way match below the touchless ceiling.

    The only shape that should ever auto-approve. If anything here routes for
    approval, straight-through processing is broken.
    """
    po = SEED_POS["PO-2001"]
    # Bill a subset of the received quantity so the total stays under the 2500
    # manufacturing ceiling once tax is applied.
    qty = Decimal(20 + (index % 25))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    return _case(
        mode=FailureMode.CLEAN_TOUCHLESS,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.AUTO_APPROVE,
            checks=_clean_checks(),
            requires_human_review=False,
            min_extraction_confidence=0.90,
            rationale=(
                "Quantity and price agree with PO-2001 and GRN-3001, arithmetic "
                "reconciles, and the total is below the touchless ceiling. The "
                "upstream PO approval is the human authorisation."
            ),
        ),
    )


def build_clean_over_threshold(index: int) -> InvoiceCase:
    """Clean match, but too large to auto-approve.

    Separates "is this invoice correct?" from "who may authorise it?" — a clean
    match is necessary but not sufficient for touchless processing.
    """
    po = SEED_POS["PO-2002"]
    qty = Decimal(120 + (index % 60))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    return _case(
        mode=FailureMode.CLEAN_OVER_THRESHOLD,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_clean_checks(),
            requires_human_review=True,
            min_extraction_confidence=0.90,
            rationale=(
                "All checks pass, but the amount exceeds the touchless ceiling, so "
                "the DOA matrix requires a human approver."
            ),
        ),
    )


# --- matching --------------------------------------------------------------


def build_missing_grn(index: int) -> InvoiceCase:
    """PO exists and matches, but nothing has been received.

    Under a pack requiring goods receipt this must not pass: paying for
    undelivered goods is precisely what three-way matching prevents.
    """
    po = SEED_POS["PO-2004"]
    qty = Decimal(10 + (index % 40))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    return _case(
        mode=FailureMode.MISSING_GRN,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_FAIL,
                    note="No goods receipt exists for PO-2004.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "PO-2004 has no goods receipt, so receipt of goods cannot be "
                "confirmed. Held rather than rejected: the GRN may simply be late."
            ),
        ),
    )


def build_quantity_overbilled(index: int) -> InvoiceCase:
    """Billed quantity exceeds what was received.

    The direct overpayment route, and the reason `allow_overbilling` defaults to
    false.
    """
    po = SEED_POS["PO-2005"]
    received = po.received_qty or Decimal(0)
    qty = received + Decimal(1 + (index % 10))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    return _case(
        mode=FailureMode.QUANTITY_OVERBILLED,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_FAIL,
                    note=f"Billed {qty} against {received} received on GRN-3005.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "Invoice bills more units than the goods receipt confirms. Paying "
                "it would be an overpayment for goods never delivered."
            ),
        ),
    )


def build_price_variance_within_tolerance(index: int) -> InvoiceCase:
    """Unit price drifts slightly above the PO, inside the 2% tolerance.

    Must auto-clear. A binary match gate would flood the exception queue with
    cases like this, which is the failure mode tolerances exist to prevent.
    """
    po = SEED_POS["PO-2007"]
    # 0.4% .. 1.8% — comfortably inside 2%.
    bump = Decimal("1") + (Decimal(4 + (index % 15)) / Decimal(1000))
    unit_price = _quantise(po.unit_price * bump)
    qty = Decimal(40 + (index % 30))
    content = _content_from_po(po, index=index, qty=qty, unit_price=unit_price)
    return _case(
        mode=FailureMode.PRICE_VARIANCE_WITHIN_TOLERANCE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.AUTO_APPROVE,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_PASS,
                    note="Price variance inside the configured tolerance.",
                ),
            ),
            min_extraction_confidence=0.90,
            rationale=(
                "Unit price is above the PO but within the tenant's price "
                "tolerance, so the variance auto-clears."
            ),
        ),
    )


def build_price_variance_exceeds_tolerance(index: int) -> InvoiceCase:
    """Unit price above the PO by more than the tolerance allows."""
    po = SEED_POS["PO-2008"]
    # 4% .. 13% — clearly outside 2%.
    bump = Decimal("1") + (Decimal(40 + (index % 90)) / Decimal(1000))
    unit_price = _quantise(po.unit_price * bump)
    qty = Decimal(60 + (index % 40))
    content = _content_from_po(po, index=index, qty=qty, unit_price=unit_price)
    return _case(
        mode=FailureMode.PRICE_VARIANCE_EXCEEDS_TOLERANCE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_FAIL,
                    note="Price variance exceeds the configured tolerance.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "Unit price exceeds the PO by more than the tenant's tolerance. "
                "Held for a buyer to confirm a price change or reject the invoice."
            ),
        ),
    )


def build_partial_delivery(index: int) -> InvoiceCase:
    """Billing exactly the partially-received quantity.

    Legitimate and common. A system that treated every short quantity as a
    mismatch would generate constant false exceptions.
    """
    po = SEED_POS["PO-2003"]
    received = po.received_qty or Decimal(0)
    # Bill at or below what was received, staying under the ceiling.
    qty = min(received, Decimal(15 + (index % 20)))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    return _case(
        mode=FailureMode.PARTIAL_DELIVERY,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.AUTO_APPROVE,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_PASS,
                    note="Billed quantity is within the partially-received quantity.",
                ),
            ),
            min_extraction_confidence=0.90,
            rationale=(
                "GRN-3003 is a partial receipt and the invoice bills no more than "
                "was received. Partial delivery is permitted by the tenant pack, "
                "so this is a clean match, not an exception."
            ),
        ),
    )


# --- PO reference ----------------------------------------------------------


def build_missing_po_reference(index: int) -> InvoiceCase:
    """No PO printed at all — the non-PO branch.

    Three-way match is not applicable, which must read as SKIP rather than PASS
    so a reviewer can tell the difference.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(10 + (index % 15))
    content = _content_from_po(
        po, index=index, qty=qty, unit_price=po.unit_price, po_reference=None
    )
    return _case(
        mode=FailureMode.MISSING_PO_REFERENCE,
        index=index,
        # Retail permits non-PO spend; manufacturing does not.
        tenant=TENANT_RETAIL,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_SKIP,
                    note="No PO reference; three-way match not applicable.",
                ),
                ExpectedCheck(name="gl_coding_grounded", verdict=_PASS),
            ),
            requires_human_review=True,
            expected_gl_account="5000",
            rationale=(
                "No PO reference, so matching cannot apply and coding cannot be "
                "inherited. GL coding must be proposed with a citation and the "
                "invoice routed to a human."
            ),
        ),
    )


def build_malformed_po_reference(index: int) -> InvoiceCase:
    """A PO reference that looks plausible but resolves to nothing.

    Distinct from a missing reference: the invoice *claims* an authorisation that
    does not exist, which is a stronger signal than simply having none.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(10 + (index % 15))
    bogus = f"PO-{9000 + index}"
    content = _content_from_po(
        po, index=index, qty=qty, unit_price=po.unit_price, po_reference=bogus
    )
    return _case(
        mode=FailureMode.MALFORMED_PO_REFERENCE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_FAIL,
                    note=f"Referenced PO {bogus} does not exist.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "The invoice cites a purchase order that cannot be found. An "
                "unresolvable authorisation reference must not be paid on trust."
            ),
        ),
    )


# --- arithmetic ------------------------------------------------------------


def build_line_sum_mismatch(index: int) -> InvoiceCase:
    """Line totals do not sum to the printed subtotal.

    Pure arithmetic, and exactly why money is Decimal: a float-based check would
    produce spurious findings on legitimate invoices and hide real ones.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(20 + (index % 20))
    base = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    drift = Decimal(5 + (index % 40))
    bad_subtotal = _quantise(base.subtotal + drift)
    tax = _tax_for(bad_subtotal)
    content = base.model_copy(
        update={
            "subtotal": bad_subtotal,
            "tax_amount": tax,
            "total_amount": _quantise(bad_subtotal + tax),
        }
    )
    return _case(
        mode=FailureMode.LINE_SUM_MISMATCH,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="math_integrity",
                    verdict=_FAIL,
                    note=f"Lines sum to {base.subtotal}, subtotal printed as {bad_subtotal}.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "The printed subtotal does not equal the sum of line totals. The "
                "document is internally inconsistent and cannot be trusted."
            ),
        ),
    )


def build_tax_total_mismatch(index: int) -> InvoiceCase:
    """Subtotal plus tax does not equal the printed total."""
    po = SEED_POS["PO-2007"]
    qty = Decimal(20 + (index % 20))
    base = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    drift = Decimal(3 + (index % 25))
    content = base.model_copy(update={"total_amount": _quantise(base.total_amount + drift)})
    return _case(
        mode=FailureMode.TAX_TOTAL_MISMATCH,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="math_integrity",
                    verdict=_FAIL,
                    note="subtotal + tax != total.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "Total does not reconcile against subtotal plus tax. Paying the "
                "printed total would overpay by the difference."
            ),
        ),
    )


def build_currency_mismatch(index: int) -> InvoiceCase:
    """Header currency differs from the currency printed on the lines.

    Genuinely dangerous: a EUR line total read as USD is a silent mispayment, and
    the domain model refuses to convert implicitly.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(15 + (index % 20))
    base = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    other = ("EUR", "GBP", "CAD")[index % 3]
    content = base.model_copy(update={"line_currency_override": other})
    return _case(
        mode=FailureMode.CURRENCY_MISMATCH,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="currency_consistency",
                    verdict=_FAIL,
                    note=f"Header currency {base.currency}, lines printed in {other}.",
                ),
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_FAIL,
                    note=f"Line currency {other} differs from PO currency {base.currency}.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                f"Line amounts are denominated in {other} while the header states "
                f"{base.currency}. No implicit conversion is permitted, so the "
                "invoice is held for clarification."
            ),
        ),
    )


# --- duplicates -----------------------------------------------------------


def build_exact_duplicate(index: int) -> InvoiceCase:
    """A verbatim resubmission of a paid bill."""
    number, vendor_id, amount, issued = HISTORICAL_EXACT_DUPLICATE
    po = SEED_POS["PO-2001"]
    qty = Decimal("125")
    content = InvoiceContent(
        invoice_number=number,
        invoice_date=issued,
        due_date=issued + timedelta(days=30),
        vendor_id=vendor_id,
        vendor_name_printed=VENDOR_NAMES[vendor_id],
        currency="USD",
        subtotal=amount,
        tax_amount=None,
        total_amount=amount,
        lines=(_line(po, qty, po.unit_price),),
        po_reference=po.doc_number,
        payment_terms="Net 30",
        remit_to_last4=VENDOR_BANK_LAST4[vendor_id],
        notes="Duplicate submission of an already-settled invoice.",
    )
    return _case(
        mode=FailureMode.EXACT_DUPLICATE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.REJECT,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="duplicate_exact",
                    verdict=_FAIL,
                    note=f"{number} already exists for {VENDOR_NAMES[vendor_id]} at {amount}.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "Vendor, invoice number, and amount all match a bill already paid. "
                "Rejected outright rather than held — there is nothing to resolve."
            ),
        ),
    )


def build_fuzzy_duplicate(index: int) -> InvoiceCase:
    """A near-miss resubmission: suffixed number, same amount, days later.

    This is the case exact matching waves through, and the reason fuzzy matching
    exists (FR-4.3).
    """
    number, vendor_id, amount, issued = HISTORICAL_FUZZY_DUPLICATE
    suffix = ("A", "B", "-1", "R", " ")[index % 5].strip()
    po = SEED_POS["PO-2003"]
    variant_number = f"{number}{suffix}" if suffix else f"{number} "
    content = InvoiceContent(
        invoice_number=variant_number.strip(),
        invoice_date=issued + timedelta(days=1 + (index % 5)),
        due_date=issued + timedelta(days=31),
        vendor_id=vendor_id,
        vendor_name_printed=VENDOR_NAMES[vendor_id],
        currency="USD",
        subtotal=amount,
        tax_amount=None,
        total_amount=amount,
        lines=(_line(po, Decimal("55"), po.unit_price),),
        po_reference=po.doc_number,
        payment_terms="Net 30",
        remit_to_last4=VENDOR_BANK_LAST4[vendor_id],
    )
    return _case(
        mode=FailureMode.FUZZY_DUPLICATE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="duplicate_fuzzy",
                    verdict=_FLAG,
                    note=f"{variant_number.strip()} closely resembles paid bill {number}.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "Invoice number differs from a paid bill by a suffix while vendor, "
                "amount, and date window match. Flagged rather than rejected: it "
                "could legitimately be a revised invoice."
            ),
        ),
    )


# --- identity and fraud ---------------------------------------------------


def build_vendor_name_ambiguous(index: int) -> InvoiceCase:
    """A printed name that sits between two real vendors.

    "Acme Corp" could be Acme Corporation or Acme Industries LLC. Guessing means
    risking payment to the wrong company, so resolution must escalate rather than
    pick the closest string.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(10 + (index % 15))
    ambiguous = ("Acme Corp", "ACME", "Acme Co.", "Acme Corp.", "acme corporation ltd")[
        index % 5
    ]
    content = _content_from_po(
        po,
        index=index,
        qty=qty,
        unit_price=po.unit_price,
        vendor_name=ambiguous,
        po_reference=None,
    )
    return _case(
        mode=FailureMode.VENDOR_NAME_AMBIGUOUS,
        index=index,
        tenant=TENANT_RETAIL,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="vendor_resolution",
                    verdict=_FLAG,
                    note=f"{ambiguous!r} matches both {VENDOR_NAMES[VENDOR_ACME]} "
                    f"and {VENDOR_NAMES[VENDOR_ACME_INDUSTRIES]}.",
                ),
                ExpectedCheck(
                    name="vendor_active",
                    verdict=_SKIP,
                    note="Vendor not resolved until identity is confirmed.",
                ),
                ExpectedCheck(
                    name="bank_detail_change",
                    verdict=_SKIP,
                    note="Bank details not evaluated without a resolved vendor.",
                ),
                ExpectedCheck(name="three_way_match", verdict=_SKIP),
            ),
            requires_human_review=True,
            rationale=(
                "The printed vendor name is ambiguous between two distinct vendors "
                "with different bank accounts. A confident guess here pays the "
                "wrong party, so identity must be confirmed by a human."
            ),
        ),
    )


def build_bank_detail_change(index: int) -> InvoiceCase:
    """Remit-to account differs from the vendor master.

    Always escalates regardless of amount (FR-4.6): altered bank details on an
    otherwise valid invoice is among the costliest AP fraud patterns.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(8 + (index % 12))
    changed_last4 = f"{(7000 + index) % 10000:04d}"
    content = _content_from_po(
        po, index=index, qty=qty, unit_price=po.unit_price, remit_last4=changed_last4
    ).model_copy(update={"remit_to_bank_name": "Coastal Commerce Bank"})
    return _case(
        mode=FailureMode.BANK_DETAIL_CHANGE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="bank_detail_change",
                    verdict=_FLAG,
                    note=f"Remit-to ends {changed_last4}; master holds "
                    f"{VENDOR_BANK_LAST4[po.vendor_id]}.",
                ),
            ),
            requires_human_review=True,
            rationale=(
                "Remit-to details differ from the vendor master. This escalates "
                "unconditionally, even though every other check passes and the "
                "amount is small — the amount is irrelevant to this risk."
            ),
        ),
    )


def build_vendor_blocked(index: int) -> InvoiceCase:
    """An invoice from a vendor blocked pending fraud investigation."""
    issued, due = _dates(index)
    amount = _quantise(Decimal(400 + (index % 600)))
    content = InvoiceContent(
        invoice_number=_invoice_number("SHL", index),
        invoice_date=issued,
        due_date=due,
        vendor_id=VENDOR_SHELL_BLOCKED,
        vendor_name_printed=VENDOR_NAMES[VENDOR_SHELL_BLOCKED],
        currency="USD",
        subtotal=amount,
        tax_amount=None,
        total_amount=amount,
        lines=(
            LineContent(
                line_number=1,
                description="Consultancy retainer",
                quantity=Decimal("1"),
                unit_price=amount,
                line_total=amount,
            ),
        ),
        po_reference=None,
        payment_terms="Due on receipt",
        remit_to_last4=VENDOR_BANK_LAST4[VENDOR_SHELL_BLOCKED],
    )
    return _case(
        mode=FailureMode.VENDOR_BLOCKED,
        index=index,
        tenant=TENANT_RETAIL,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.REJECT,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="vendor_active",
                    verdict=_FAIL,
                    note="Vendor is blocked pending investigation.",
                ),
                ExpectedCheck(name="three_way_match", verdict=_SKIP),
            ),
            requires_human_review=True,
            rationale=(
                "The vendor is blocked. No amount and no other passing check makes "
                "this payable, so it is rejected rather than held."
            ),
        ),
    )


def build_vendor_inactive(index: int) -> InvoiceCase:
    """An invoice from a deactivated vendor."""
    issued, due = _dates(index)
    amount = _quantise(Decimal(200 + (index % 500)))
    content = InvoiceContent(
        invoice_number=_invoice_number("UMB", index),
        invoice_date=issued,
        due_date=due,
        vendor_id=VENDOR_UMBRELLA_INACTIVE,
        vendor_name_printed=VENDOR_NAMES[VENDOR_UMBRELLA_INACTIVE],
        currency="USD",
        subtotal=amount,
        tax_amount=None,
        total_amount=amount,
        lines=(
            LineContent(
                line_number=1,
                description="Janitorial supplies, monthly",
                quantity=Decimal("1"),
                unit_price=amount,
                line_total=amount,
            ),
        ),
        po_reference=None,
        payment_terms="Net 30",
        remit_to_last4=VENDOR_BANK_LAST4[VENDOR_UMBRELLA_INACTIVE],
    )
    return _case(
        mode=FailureMode.VENDOR_INACTIVE,
        index=index,
        tenant=TENANT_RETAIL,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.HOLD,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="vendor_active",
                    verdict=_FAIL,
                    note="Vendor record is inactive.",
                ),
                ExpectedCheck(name="three_way_match", verdict=_SKIP),
            ),
            requires_human_review=True,
            rationale=(
                "Vendor is inactive. Held rather than rejected: reactivation may be "
                "legitimate, unlike a blocked vendor."
            ),
        ),
    )


def build_threshold_avoidance(index: int) -> InvoiceCase:
    """An amount parked just below an approval boundary.

    Sharpened by pairing with a recently-created vendor: a new payee invoicing
    just under a threshold is a documented intake red flag.
    """
    po = SEED_POS["PO-2006"]
    boundary = Decimal("5000.00")
    # Land 0.2% .. 2.2% under the boundary, before tax.
    shortfall = _quantise(boundary * (Decimal(2 + (index % 20)) / Decimal(1000)))
    target_total = _quantise(boundary - shortfall)
    subtotal = _quantise(target_total / (Decimal(1) + TAX_RATE))
    tax = _quantise(target_total - subtotal)
    qty = Decimal("1")

    issued, due = _dates(index)
    content = InvoiceContent(
        invoice_number=_invoice_number("NWT", index),
        invoice_date=issued,
        due_date=due,
        vendor_id=VENDOR_NORTHWIND_NEW,
        vendor_name_printed=VENDOR_NAMES[VENDOR_NORTHWIND_NEW],
        currency="USD",
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=target_total,
        lines=(
            LineContent(
                line_number=1,
                description=po.line_description,
                quantity=qty,
                unit_price=subtotal,
                line_total=subtotal,
            ),
        ),
        po_reference=po.doc_number,
        payment_terms="Net 30",
        remit_to_last4=VENDOR_BANK_LAST4[VENDOR_NORTHWIND_NEW],
    )
    return _case(
        mode=FailureMode.THRESHOLD_AVOIDANCE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="threshold_avoidance",
                    verdict=_FLAG,
                    note=f"Total {target_total} sits just below the {boundary} DOA boundary.",
                ),
                ExpectedCheck(name="new_vendor", verdict=_FLAG),
            ),
            requires_human_review=True,
            rationale=(
                "Amount is parked immediately below an approval boundary and the "
                "vendor was created weeks ago. Individually weak signals; together "
                "they warrant human eyes."
            ),
        ),
    )


# --- coding ---------------------------------------------------------------


def build_non_po_services(index: int) -> InvoiceCase:
    """A services invoice with no PO, needing GL coding from policy.

    The case that justifies having an agent at all: coding cannot be inherited
    and cannot be derived arithmetically, so it requires reading and reasoning
    grounded in retrieved policy and precedent.
    """
    issued, due = _dates(index)
    amount = _quantise(Decimal(600 + (index % 900)))
    descriptions = (
        ("Contract engineering support, monthly", "6500"),
        ("Analytics platform licence renewal", "7200"),
        ("Printer toner and paper, bulk", "6410"),
        ("Laptop docking stations", "6420"),
        ("Trade show booth production", "6600"),
    )
    description, expected_account = descriptions[index % len(descriptions)]

    content = InvoiceContent(
        invoice_number=_invoice_number("SVC", index),
        invoice_date=issued,
        due_date=due,
        vendor_id=VENDOR_INITECH,
        vendor_name_printed=VENDOR_NAMES[VENDOR_INITECH],
        currency="USD",
        subtotal=amount,
        tax_amount=None,
        total_amount=amount,
        lines=(
            LineContent(
                line_number=1,
                description=description,
                quantity=Decimal("1"),
                unit_price=amount,
                line_total=amount,
            ),
        ),
        po_reference=None,
        payment_terms="Net 30",
        remit_to_last4=VENDOR_BANK_LAST4[VENDOR_INITECH],
    )
    return _case(
        mode=FailureMode.NON_PO_SERVICES,
        index=index,
        tenant=TENANT_RETAIL,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(name="three_way_match", verdict=_SKIP),
                ExpectedCheck(
                    name="gl_coding_grounded",
                    verdict=_PASS,
                    note="Proposed account must carry a policy or precedent citation.",
                ),
            ),
            requires_human_review=True,
            expected_gl_account=expected_account,
            rationale=(
                f"Non-PO services invoice. Coding must be proposed as {expected_account} "
                "with a supporting citation; uncited coding may not be auto-applied."
            ),
        ),
    )


# --- document quality ----------------------------------------------------


def build_label_variation(index: int) -> InvoiceCase:
    """Correct data behind unusual field labels.

    Stresses semantic mapping specifically: nothing is wrong with the invoice,
    only with how a naive keyword matcher would read it.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(12 + (index % 18))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    return _case(
        mode=FailureMode.LABEL_VARIATION,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.AUTO_APPROVE,
            checks=_clean_checks(),
            min_extraction_confidence=0.75,
            rationale=(
                "The invoice is entirely valid; only the field labels are unusual. "
                "Extraction must map them to canonical fields and the case must "
                "auto-approve — a mapping failure here shows up as a false exception."
            ),
        ),
    )


def build_ocr_noise(index: int) -> InvoiceCase:
    """A degraded scan with no text layer.

    Confidence is expected to drop; the point is that it drops *and is measured*,
    so the confidence gate on auto-approval is exercised rather than assumed.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(10 + (index % 15))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    noise = 0.25 + (index % 7) * 0.09  # 0.25 .. 0.79
    rotation = (-1.6, -0.8, 0.0, 0.9, 1.7)[index % 5]
    return _case(
        mode=FailureMode.OCR_NOISE,
        index=index,
        content=content,
        render_format=RenderFormat.PDF_SCANNED,
        noise=round(min(noise, 0.85), 3),
        rotation=rotation,
        dpi=150,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="extraction_confidence",
                    verdict=_FLAG,
                    note="Degraded scan; confidence below the touchless threshold.",
                ),
            ),
            requires_human_review=True,
            min_extraction_confidence=0.40,
            rationale=(
                "Content is valid but the scan is degraded, so extraction "
                "confidence should fall below the touchless threshold and the "
                "invoice should route for review rather than auto-approve on a "
                "possibly-misread total."
            ),
        ),
    )


def build_image_only(index: int) -> InvoiceCase:
    """A photographed invoice delivered as a bare image."""
    po = SEED_POS["PO-2007"]
    qty = Decimal(10 + (index % 12))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    return _case(
        mode=FailureMode.IMAGE_ONLY,
        index=index,
        content=content,
        render_format=RenderFormat.IMAGE,
        noise=round(0.12 + (index % 5) * 0.06, 3),
        rotation=(-1.0, 0.0, 1.2)[index % 3],
        dpi=150,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="extraction_confidence",
                    verdict=_FLAG,
                    note="Image-only input; no text layer available.",
                ),
            ),
            requires_human_review=True,
            min_extraction_confidence=0.45,
            rationale=(
                "Delivered as an image with no text layer, so the OCR path must "
                "handle it. Reduced confidence should prevent auto-approval."
            ),
        ),
    )


# --- real-world layout complexity -----------------------------------------
# Research-grounded (see INC-006): each type maps to a documented real AP
# failure mode and exercises a specific check, not generic "hard document"
# difficulty. Handwriting and genuine fax artefacts are deliberately excluded
# — a synthetic fake of either teaches nothing real.


def build_credit_note(index: int) -> InvoiceCase:
    """A vendor credit memo: negative amounts crediting money back.

    Standard AP practice (Accounting Seed, Stripe): a credit note mirrors an
    invoice but with negative payable lines. It must NOT read as an arithmetic
    error — a negative total is correct here — and it always routes for human
    handling because applying a credit is a deliberate act, never touchless.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(10 + (index % 15))
    unit_price = po.unit_price
    line_total = -_quantise(qty * unit_price)
    subtotal = line_total
    tax = _quantise(subtotal * TAX_RATE)
    total = _quantise(subtotal + tax)
    issued, due = _dates(index)

    content = InvoiceContent(
        invoice_number=_invoice_number("CN", index),
        invoice_date=issued,
        due_date=due,
        vendor_id=po.vendor_id,
        vendor_name_printed=VENDOR_NAMES[po.vendor_id],
        currency="USD",
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=total,
        lines=(
            LineContent(
                line_number=1,
                description=f"Credit: {po.line_description} (returned)",
                quantity=qty,
                unit_price=unit_price,
                line_total=line_total,
            ),
        ),
        po_reference=po.doc_number,
        payment_terms="Net 30",
        remit_to_last4=VENDOR_BANK_LAST4[po.vendor_id],
        document_kind=DocumentKind.CREDIT_NOTE,
        notes="Credit note against previously invoiced goods returned to supplier.",
    )
    return _case(
        mode=FailureMode.CREDIT_NOTE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                # Arithmetic still reconciles — just with signed amounts.
                ExpectedCheck(
                    name="math_integrity",
                    verdict=_PASS,
                    note="Signed amounts reconcile: line total, subtotal, and total are consistently negative.",
                ),
                # A reversal does not forward-match a positive-quantity PO.
                ExpectedCheck(
                    name="three_way_match",
                    verdict=_SKIP,
                    note="Credit note; forward three-way match does not apply to a reversal.",
                ),
            ),
            requires_human_review=True,
            min_extraction_confidence=0.85,
            rationale=(
                "A credit note is a legitimate negative-amount document, not an "
                "arithmetic defect. Arithmetic reconciles with signed amounts, "
                "forward matching is skipped because a reversal has no PO "
                "quantities to match, and it routes to a human because applying "
                "a credit is a deliberate act."
            ),
        ),
    )


def build_multi_line_tax(index: int) -> InvoiceCase:
    """Two tax rows in the totals block (multi-jurisdiction).

    A single-tax totals model cannot represent state + city sales tax, or VAT
    plus an environmental levy. `math_integrity` must reconcile subtotal plus
    the *sum* of tax rows against the total, not assume one tax line.
    """
    po = SEED_POS["PO-2002"]
    qty = Decimal(20 + (index % 20))
    base = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price, include_tax=False)
    subtotal = base.subtotal
    state = _quantise(subtotal * Decimal("0.06"))
    city = _quantise(subtotal * Decimal("0.0225"))
    tax_total = _quantise(state + city)
    total = _quantise(subtotal + tax_total)

    content = base.model_copy(
        update={
            "tax_amount": tax_total,
            "total_amount": total,
            "tax_lines": (
                TaxLine(label="State Tax", amount=state),
                TaxLine(label="City Tax", amount=city),
            ),
        }
    )
    return _case(
        mode=FailureMode.MULTI_LINE_TAX,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_clean_checks(),
            requires_human_review=True,
            min_extraction_confidence=0.85,
            rationale=(
                "A correct invoice with two tax rows. Extraction must sum the "
                "tax lines and math must reconcile subtotal + total tax = total. "
                "Above the manufacturing touchless ceiling, so a human approves."
            ),
        ),
    )


def build_reverse_charge_vat(index: int) -> InvoiceCase:
    """Cross-border B2B invoice: 0% VAT with a reverse-charge note.

    Under the EU VAT domestic reverse charge (S55A), the supplier charges no
    VAT and the buyer accounts for it. The tax line is a legitimate 0.00, so a
    check that expected tax > 0 would wrongly flag a perfectly valid invoice.
    """
    po = SEED_POS["PO-2003"]
    qty = min(po.received_qty or Decimal(0), Decimal(15 + (index % 20)))
    base = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price, include_tax=False)
    subtotal = base.subtotal

    content = base.model_copy(
        update={
            "tax_amount": Decimal("0.00"),
            "total_amount": subtotal,
            "tax_lines": (
                TaxLine(label="VAT (reverse charge)", amount=Decimal("0.00"), is_reverse_charge=True),
            ),
            "notes": (
                "Reverse charge: customer to account for VAT to HMRC. "
                "VAT Act 1994 Section 55A applies."
            ),
        }
    )
    return _case(
        mode=FailureMode.REVERSE_CHARGE_VAT,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="math_integrity",
                    verdict=_PASS,
                    note="Zero VAT is correct under reverse charge; subtotal equals total.",
                ),
            ),
            requires_human_review=True,
            min_extraction_confidence=0.85,
            rationale=(
                "A reverse-charge invoice legitimately shows 0% VAT with the buyer "
                "accounting for the tax. The 0.00 tax line is correct, not a defect, "
                "and math reconciles with subtotal equal to total."
            ),
        ),
    )


def build_multi_page(index: int) -> InvoiceCase:
    """A line table long enough to spill onto a second page.

    Tests that parsing keeps line items across a page break and that
    `completeness` and the line sum still hold when the table is not contained
    on one page.
    """
    po = SEED_POS["PO-2002"]
    # Enough distinct lines to overflow a single page's table region. The
    # first-page table has room for roughly 30 rows before the totals block is
    # forced down, so 38+ guarantees a genuine page break rather than a table
    # that merely looks long.
    line_count = 38 + (index % 8)
    lines = tuple(
        LineContent(
            line_number=n + 1,
            description=f"{po.line_description} — batch {n + 1:02d}",
            quantity=Decimal(2 + (n % 5)),
            unit_price=po.unit_price,
            line_total=_quantise(Decimal(2 + (n % 5)) * po.unit_price),
        )
        for n in range(line_count)
    )
    subtotal = _quantise(sum((line.line_total for line in lines), Decimal(0)))
    tax = _tax_for(subtotal)
    total = _quantise(subtotal + tax)
    issued, due = _dates(index)

    content = InvoiceContent(
        invoice_number=_invoice_number("MP", index),
        invoice_date=issued,
        due_date=due,
        vendor_id=po.vendor_id,
        vendor_name_printed=VENDOR_NAMES[po.vendor_id],
        currency="USD",
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=total,
        lines=lines,
        po_reference=po.doc_number,
        payment_terms="Net 30",
        remit_to_last4=VENDOR_BANK_LAST4[po.vendor_id],
    )
    return _case(
        mode=FailureMode.MULTI_PAGE,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(_clean_checks(), _threshold_avoidance_expectation(total)),
            requires_human_review=True,
            min_extraction_confidence=0.80,
            rationale=(
                "A valid invoice whose line table spans two pages. Extraction must "
                "recover every line across the page break; the amount exceeds the "
                "touchless ceiling so a human approves."
            ),
        ),
    )


def build_foreign_currency(index: int) -> InvoiceCase:
    """A wholly self-consistent non-USD invoice.

    Distinct from CURRENCY_MISMATCH (which mixes currencies within one
    document): here every amount is in EUR or GBP, printed with the currency
    symbol. Tests currency-handling breadth without an actual defect.
    """
    po = SEED_POS["PO-2007"]
    ccy, symbol = (("EUR", "\u20ac"), ("GBP", "\u00a3"))[index % 2]
    qty = Decimal(10 + (index % 12))
    unit_price = po.unit_price
    subtotal = _quantise(qty * unit_price)
    tax = _tax_for(subtotal)
    total = _quantise(subtotal + tax)
    issued, due = _dates(index)

    content = InvoiceContent(
        invoice_number=_invoice_number("FX", index),
        invoice_date=issued,
        due_date=due,
        vendor_id=po.vendor_id,
        vendor_name_printed=VENDOR_NAMES[po.vendor_id],
        currency=ccy,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=total,
        lines=(_line(po, qty, unit_price),),
        po_reference=po.doc_number,
        payment_terms="Net 30",
        remit_to_last4=VENDOR_BANK_LAST4[po.vendor_id],
        currency_symbol=symbol,
    )
    return _case(
        mode=FailureMode.FOREIGN_CURRENCY,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                # A foreign-currency invoice is never touchless (thresholds are
                # denominated in the tenant base currency), so it routes for
                # approval even though nothing is wrong.
                ExpectedCheck(name="currency_consistency", verdict=_PASS),
                ExpectedCheck(
                    name="threshold_avoidance",
                    verdict=_SKIP,
                    note="DOA thresholds are denominated in the tenant base currency.",
                ),
            ),
            requires_human_review=True,
            min_extraction_confidence=0.85,
            rationale=(
                f"A fully valid {ccy} invoice. Every amount shares one non-USD "
                "currency, so it is internally consistent, but it cannot be "
                "touchless in a USD-denominated tenant and routes for approval."
            ),
        ),
    )


def build_logo_overlap(index: int) -> InvoiceCase:
    """A vendor logo block crowding the header text.

    A real letterhead often sits over or beside the invoice number and date.
    Tests that extraction (and structural label recovery) survives a logo
    region competing with the header fields for space.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(12 + (index % 18))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price).model_copy(
        update={"has_logo": True}
    )
    return _case(
        mode=FailureMode.LOGO_OVERLAP,
        index=index,
        content=content,
        expectation=CaseExpectation(
            route=ExpectedRoute.AUTO_APPROVE,
            checks=_clean_checks(),
            min_extraction_confidence=0.80,
            rationale=(
                "A valid, in-tolerance invoice whose header is crowded by a vendor "
                "logo. Extraction must read the header fields despite the logo; a "
                "mapping failure here would be a false exception."
            ),
        ),
    )


def build_heavy_skew(index: int) -> InvoiceCase:
    """A scan rotated several degrees — the range a fax or phone photo hits.

    OCR_NOISE uses sub-2-degree drift; real scans routinely arrive at 3-6
    degrees, which is where deskewing and OCR both start to struggle. Confidence
    should drop and the invoice route for review rather than auto-approve on a
    possibly-misread total.
    """
    po = SEED_POS["PO-2001"]
    qty = Decimal(10 + (index % 15))
    content = _content_from_po(po, index=index, qty=qty, unit_price=po.unit_price)
    rotation = (-5.5, -4.0, 3.5, 4.5, 6.0)[index % 5]
    return _case(
        mode=FailureMode.HEAVY_SKEW,
        index=index,
        content=content,
        render_format=RenderFormat.PDF_SCANNED,
        noise=round(0.2 + (index % 5) * 0.06, 3),
        rotation=rotation,
        dpi=150,
        expectation=CaseExpectation(
            route=ExpectedRoute.ROUTE_FOR_APPROVAL,
            checks=_override(
                _clean_checks(),
                ExpectedCheck(
                    name="extraction_confidence",
                    verdict=_FLAG,
                    note="Heavily skewed scan; confidence below the touchless threshold.",
                ),
            ),
            requires_human_review=True,
            min_extraction_confidence=0.35,
            rationale=(
                "Content is valid but the page is rotated several degrees, the "
                "range where deskew and OCR degrade. Confidence should fall below "
                "the touchless threshold and route for review."
            ),
        ),
    )


# --------------------------------------------------------------------- registry

BUILDERS: dict[FailureMode, CaseBuilder] = {
    FailureMode.CLEAN_TOUCHLESS: build_clean_touchless,
    FailureMode.CLEAN_OVER_THRESHOLD: build_clean_over_threshold,
    FailureMode.MISSING_GRN: build_missing_grn,
    FailureMode.QUANTITY_OVERBILLED: build_quantity_overbilled,
    FailureMode.PRICE_VARIANCE_WITHIN_TOLERANCE: build_price_variance_within_tolerance,
    FailureMode.PRICE_VARIANCE_EXCEEDS_TOLERANCE: build_price_variance_exceeds_tolerance,
    FailureMode.PARTIAL_DELIVERY: build_partial_delivery,
    FailureMode.MISSING_PO_REFERENCE: build_missing_po_reference,
    FailureMode.MALFORMED_PO_REFERENCE: build_malformed_po_reference,
    FailureMode.LINE_SUM_MISMATCH: build_line_sum_mismatch,
    FailureMode.TAX_TOTAL_MISMATCH: build_tax_total_mismatch,
    FailureMode.CURRENCY_MISMATCH: build_currency_mismatch,
    FailureMode.EXACT_DUPLICATE: build_exact_duplicate,
    FailureMode.FUZZY_DUPLICATE: build_fuzzy_duplicate,
    FailureMode.VENDOR_NAME_AMBIGUOUS: build_vendor_name_ambiguous,
    FailureMode.BANK_DETAIL_CHANGE: build_bank_detail_change,
    FailureMode.VENDOR_BLOCKED: build_vendor_blocked,
    FailureMode.VENDOR_INACTIVE: build_vendor_inactive,
    FailureMode.THRESHOLD_AVOIDANCE: build_threshold_avoidance,
    FailureMode.NON_PO_SERVICES: build_non_po_services,
    FailureMode.LABEL_VARIATION: build_label_variation,
    FailureMode.OCR_NOISE: build_ocr_noise,
    FailureMode.IMAGE_ONLY: build_image_only,
    FailureMode.CREDIT_NOTE: build_credit_note,
    FailureMode.MULTI_LINE_TAX: build_multi_line_tax,
    FailureMode.REVERSE_CHARGE_VAT: build_reverse_charge_vat,
    FailureMode.MULTI_PAGE: build_multi_page,
    FailureMode.FOREIGN_CURRENCY: build_foreign_currency,
    FailureMode.LOGO_OVERLAP: build_logo_overlap,
    FailureMode.HEAVY_SKEW: build_heavy_skew,
}


def build_case(mode: FailureMode, index: int) -> InvoiceCase:
    return BUILDERS[mode](index)


def all_modes() -> tuple[FailureMode, ...]:
    return tuple(BUILDERS)
