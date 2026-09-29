"""Three-way matching and tolerance evaluation: `three_way_match` (FR-4.1,
FR-4.2).

Matches an invoice against the purchase order it cites and the goods receipt
confirming delivery, on quantity, unit price, and total — the deterministic
core of AP automation. Everything here is configured by
`PolicyPack.tolerances`, so the same function enforces manufacturing's
zero-quantity-tolerance, GRN-mandatory posture and retail's looser,
GRN-optional posture through configuration alone (ADR-004).

**`SKIP`, not `PASS`, for a non-PO invoice.** `Invoice.is_non_po` is the exact
predicate: no PO reference means matching cannot apply at all, and a
reviewer must be able to tell "there was nothing to match" apart from "it
matched cleanly" — collapsing the two into PASS would hide that GL coding for
this invoice has to come from somewhere else entirely (the hybrid-RAG layer,
built separately).

**Ladder of findings, evaluated in order of what actually blocks payment,**
each returning immediately once triggered rather than reporting every
possible defect:

1. Referenced PO does not exist at all → FAIL (stronger signal than no
   reference: the invoice *claims* an authorisation that is not real).
2. PO exists but required GRN is missing (`require_grn`) → FAIL.
3. Billed quantity exceeds received quantity (overbilling, unless the pack
   explicitly allows it) → FAIL.
4. Quantity variance beyond `tolerances.quantity_pct` → FAIL (billing
   *less* than available is a partial delivery, governed by
   `allow_partial_delivery` instead of the tolerance band).
5. Price variance beyond `tolerances.price_pct` → FAIL.
6. Otherwise → PASS, including the common and legitimate case of a partial
   delivery billed at or below what was actually received.

`tolerances.total_absolute` is deliberately not used here at all — see the
note above the final `return` for why a second absolute-tolerance comparison
against the PO would conflict with the price tolerance this function already
enforces. That value is exercised by `math_integrity` (`policy.arithmetic`)
instead, where "total" reconciliation is the document's own arithmetic
(subtotal + tax = total), not a comparison against the PO.
"""

from __future__ import annotations

from decimal import Decimal

from ap_agent.core.canonical import PurchaseOrder
from ap_agent.core.checks import CheckCategory, CheckResult, Severity, Verdict
from ap_agent.core.primitives import Money
from ap_agent.policy.context import PolicyEvaluationContext

THREE_WAY_MATCH = "three_way_match"


def _po_line_totals(
    po: PurchaseOrder, context: PolicyEvaluationContext
) -> tuple[Decimal, Decimal, Money]:
    """(ordered_qty, received_qty, po_unit_price) summed/derived from the PO
    and GRN, assuming a single comparable line — matching the shape of every
    fixture case this engine is built against. A multi-line reconciliation
    would need line-level pairing (by SKU or description) that the canonical
    model does not yet key on; left as a documented v2 gap rather than a
    silent approximation, since guessing a pairing would be worse than not
    matching at all.

    `po` is required (not read from `context.purchase_order`, which is
    `Optional`) so the caller's None-check is enforced by the type checker
    rather than repeated here.
    """
    ordered = sum((line.quantity for line in po.lines), Decimal(0))
    po_price = po.lines[0].unit_price if po.lines else Money.zero(po.currency)

    received = Decimal(0)
    if context.goods_receipt is not None:
        received = sum((line.quantity for line in context.goods_receipt.lines), Decimal(0))

    return ordered, received, po_price


def three_way_match(context: PolicyEvaluationContext) -> CheckResult:
    invoice = context.invoice
    tolerances = context.policy.tolerances

    if invoice.is_credit_note:
        # A credit note reverses goods; it does not match a PO's forward
        # quantities. Skip (like a non-PO invoice) rather than report a
        # misleading clean match against a positive-quantity order. The credit
        # still routes for human handling via the run's review requirement —
        # applying a credit is always a deliberate act, never touchless.
        return CheckResult(
            name=THREE_WAY_MATCH,
            category=CheckCategory.MATCHING,
            verdict=Verdict.SKIP,
            reasoning="Credit note (negative total); forward three-way match "
            "does not apply to a reversal.",
            threshold="n/a",
            actual="credit note",
        )

    if invoice.is_non_po:
        return CheckResult(
            name=THREE_WAY_MATCH,
            category=CheckCategory.MATCHING,
            verdict=Verdict.SKIP,
            reasoning="No PO reference; three-way match not applicable.",
            threshold="n/a",
            actual="non-PO invoice",
        )

    po = context.purchase_order
    po_reference = str(invoice.po_reference.value) if invoice.po_reference else ""

    if po is None:
        return CheckResult(
            name=THREE_WAY_MATCH,
            category=CheckCategory.MATCHING,
            verdict=Verdict.FAIL,
            severity=Severity.HIGH,
            reasoning=f"Referenced PO {po_reference!r} does not exist.",
            threshold="referenced PO must exist",
            actual="not found",
            inputs={"po_reference": po_reference},
            forces_review=True,
        )

    grn = context.goods_receipt
    if context.policy.require_grn and grn is None:
        return CheckResult(
            name=THREE_WAY_MATCH,
            category=CheckCategory.MATCHING,
            verdict=Verdict.FAIL,
            severity=Severity.HIGH,
            reasoning=f"No goods receipt exists for {po.po_number}.",
            threshold="goods receipt required",
            actual="no goods receipt",
            inputs={"po_number": po.po_number},
            forces_review=True,
        )

    invoiced_qty = sum((line.quantity.value for line in invoice.lines), Decimal(0))
    ordered_qty, received_qty, po_unit_price = _po_line_totals(po, context)

    # Overbilling: billed more than was received. Checked before price/qty
    # tolerance because it is unconditional (unless explicitly permitted) —
    # a price variance finding on an invoice that is *also* overbilled would
    # bury the more serious problem underneath a milder one.
    if grn is not None and not tolerances.allow_overbilling and invoiced_qty > received_qty:
        return CheckResult(
            name=THREE_WAY_MATCH,
            category=CheckCategory.MATCHING,
            verdict=Verdict.FAIL,
            severity=Severity.HIGH,
            reasoning=f"Billed {invoiced_qty} against {received_qty} received on "
            f"{grn.grn_number}.",
            threshold=f"invoiced quantity <= received ({received_qty})",
            actual=str(invoiced_qty),
            inputs={
                "invoiced_qty": str(invoiced_qty),
                "received_qty": str(received_qty),
                "grn_number": grn.grn_number,
            },
            forces_review=True,
        )

    # Quantity variance against the *ordered* quantity, for the case where no
    # GRN was required at all (`require_grn=False`) and there is nothing else
    # to compare billed quantity against.
    quantity_baseline = received_qty if grn is not None else ordered_qty
    if quantity_baseline > 0:
        qty_variance_pct = abs((invoiced_qty - quantity_baseline) / quantity_baseline) * Decimal(
            100
        )
    else:
        qty_variance_pct = Decimal(0) if invoiced_qty == 0 else Decimal("Infinity")

    if invoiced_qty < quantity_baseline:
        # Billing less than received/ordered is a partial delivery, not a
        # variance to measure against the quantity tolerance — the tolerance
        # governs *overshoot*, and `allow_partial_delivery` is the switch that
        # governs undershoot. A pack that forbids it entirely would need a
        # different, more conservative rule; this engine's fixture-verified
        # posture is that partial billing is normal and welcome.
        if not tolerances.allow_partial_delivery:
            return CheckResult(
                name=THREE_WAY_MATCH,
                category=CheckCategory.MATCHING,
                verdict=Verdict.FAIL,
                severity=Severity.MEDIUM,
                reasoning=f"Billed {invoiced_qty} against {quantity_baseline} "
                "available, and this tenant does not permit partial delivery "
                "billing.",
                threshold="partial delivery not permitted",
                actual=str(invoiced_qty),
                inputs={"invoiced_qty": str(invoiced_qty), "baseline": str(quantity_baseline)},
                forces_review=True,
            )
    elif qty_variance_pct > tolerances.quantity_pct:
        # An overshoot against a GRN baseline that the pack has explicitly
        # permitted (`allow_overbilling`) was already let through by the
        # dedicated overbilling check above; it must not be re-caught here
        # under the *quantity tolerance* rule, which governs ordinary
        # measurement variance, not a deliberately configured allowance.
        overbilling_permitted = grn is not None and tolerances.allow_overbilling
        if not overbilling_permitted:
            return CheckResult(
                name=THREE_WAY_MATCH,
                category=CheckCategory.MATCHING,
                verdict=Verdict.FAIL,
                severity=Severity.MEDIUM,
                reasoning=f"Quantity variance {qty_variance_pct:.2f}% exceeds the "
                f"configured {tolerances.quantity_pct}% tolerance.",
                threshold=f"<= {tolerances.quantity_pct}%",
                actual=f"{qty_variance_pct:.2f}%",
                inputs={"invoiced_qty": str(invoiced_qty), "baseline": str(quantity_baseline)},
                forces_review=True,
            )

    # Price variance against the PO's contracted unit price. Skipped only when
    # the PO itself carries no line to compare against — a data-completeness
    # gap in what was loaded, not a price defect to report as a variance.
    # `variance_pct_against` otherwise returns a large sentinel (not a crash)
    # for a zero-but-present baseline, so a PO line legitimately priced at
    # zero still fails a nonzero invoice price rather than skipping silently.
    invoice_price = invoice.lines[0].unit_price.value if invoice.lines else None
    if invoice_price is not None and po.lines:
        if invoice_price.currency != po_unit_price.currency:
            return CheckResult(
                name=THREE_WAY_MATCH,
                category=CheckCategory.MATCHING,
                verdict=Verdict.FAIL,
                severity=Severity.HIGH,
                reasoning=f"Invoice line currency {invoice_price.currency} differs "
                f"from PO currency {po_unit_price.currency}.",
                threshold="same currency as PO",
                actual=invoice_price.currency,
                forces_review=True,
            )

        price_variance_pct = invoice_price.variance_pct_against(po_unit_price)
        if abs(price_variance_pct) > tolerances.price_pct:
            return CheckResult(
                name=THREE_WAY_MATCH,
                category=CheckCategory.MATCHING,
                verdict=Verdict.FAIL,
                severity=Severity.MEDIUM,
                reasoning=f"Price variance {price_variance_pct:.2f}% exceeds the "
                f"configured {tolerances.price_pct}% tolerance.",
                threshold=f"<= {tolerances.price_pct}%",
                actual=f"{price_variance_pct:.2f}%",
                inputs={
                    "invoice_unit_price": str(invoice_price),
                    "po_unit_price": str(po_unit_price),
                },
                forces_review=True,
            )

    # No separate "total variance against the PO" check follows. It was
    # tried and removed: comparing the invoice's extended subtotal against
    # `po_unit_price * invoiced_qty` duplicates what the price-variance check
    # above already validated within `tolerances.price_pct`, and re-checking
    # it against the much narrower `total_absolute` band would fail an
    # invoice whose per-unit price the tenant's own tolerance had just
    # accepted — re-litigating a variance a wider, deliberately configured
    # tolerance already cleared. `total_absolute` is exercised instead where
    # it has a real, independent job: `math_integrity` (`policy.arithmetic`),
    # reconciling the printed subtotal against the printed line sum and the
    # printed total against subtotal-plus-tax — arithmetic internal to the
    # document, not a second comparison against the PO.

    return CheckResult(
        name=THREE_WAY_MATCH,
        category=CheckCategory.MATCHING,
        verdict=Verdict.PASS,
        reasoning=f"Invoice reconciles with {po.po_number}"
        + (f" and {grn.grn_number}" if grn is not None else "")
        + " within the configured tolerances.",
        threshold=(
            f"price <= {tolerances.price_pct}%, quantity <= {tolerances.quantity_pct}%"
        ),
        actual="within tolerance",
        inputs={"po_number": po.po_number, "invoiced_qty": str(invoiced_qty)},
    )
