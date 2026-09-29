"""`three_way_match` (FR-4.1, FR-4.2).

Each test mirrors one of the adversarial fixture cases in
`apfixtures.cases` by name in its docstring, so a fixture-set change and an
engine-behaviour change can be told apart.
"""

from __future__ import annotations

from decimal import Decimal

from ap_agent.core.checks import Verdict
from ap_agent.policy.matching import THREE_WAY_MATCH, three_way_match
from tests.unit.policy_fixtures import (
    context,
    goods_receipt,
    invoice,
    invoice_line,
    policy_pack,
    purchase_order,
)


class TestNonPoSkip:
    def test_no_po_reference_skips(self) -> None:
        """Mirrors `build_missing_po_reference`."""
        inv = invoice(po_reference=None)
        result = three_way_match(context(inv=inv))
        assert result.verdict is Verdict.SKIP
        assert result.name == THREE_WAY_MATCH

    def test_blank_po_reference_also_skips(self) -> None:
        inv = invoice(po_reference="   ")
        result = three_way_match(context(inv=inv))
        assert result.verdict is Verdict.SKIP


class TestCreditNoteSkip:
    def test_negative_total_credit_note_skips_even_with_a_po(self) -> None:
        """Mirrors `build_credit_note`: a reversal does not forward-match a PO."""
        po = purchase_order(po_number="PO-2001", quantity="120", unit_price="12.00")
        grn = goods_receipt(po_number="PO-2001", quantity="120", unit_price="12.00")
        inv = invoice(
            po_reference="PO-2001",
            subtotal="-240.00",
            tax_amount="-19.80",
            total_amount="-259.80",
            lines=(invoice_line(quantity="20", unit_price="12.00", line_total="-240.00"),),
        )
        result = three_way_match(context(inv=inv, po=po, grn=grn))
        assert result.verdict is Verdict.SKIP
        assert result.actual == "credit note"


class TestMalformedPoReference:
    def test_po_reference_with_no_matching_po_fails(self) -> None:
        """Mirrors `build_malformed_po_reference`."""
        inv = invoice(po_reference="PO-9999")
        result = three_way_match(context(inv=inv, po=None))
        assert result.verdict is Verdict.FAIL
        assert result.forces_review is True
        assert "PO-9999" in result.reasoning
        assert "does not exist" in result.reasoning


class TestMissingGrn:
    def test_grn_required_but_absent_fails(self) -> None:
        """Mirrors `build_missing_grn`."""
        po = purchase_order(po_number="PO-2004", quantity="80", unit_price="40.00")
        inv = invoice(
            po_reference="PO-2004",
            lines=(invoice_line(quantity="30", unit_price="40.00", line_total="1200.00"),),
            subtotal="1200.00",
            tax_amount=None,
            total_amount="1200.00",
        )
        result = three_way_match(context(inv=inv, po=po, grn=None))
        assert result.verdict is Verdict.FAIL
        assert "No goods receipt" in result.reasoning

    def test_grn_not_required_and_absent_does_not_fail_for_that_reason(self) -> None:
        pack = policy_pack(require_grn=False)
        po = purchase_order(po_number="PO-2004", quantity="80", unit_price="40.00")
        inv = invoice(
            po_reference="PO-2004",
            lines=(invoice_line(quantity="80", unit_price="40.00", line_total="3200.00"),),
            subtotal="3200.00",
            tax_amount=None,
            total_amount="3200.00",
        )
        result = three_way_match(context(inv=inv, policy=pack, po=po, grn=None))
        assert result.verdict is Verdict.PASS


class TestOverbilling:
    def test_billed_more_than_received_fails(self) -> None:
        """Mirrors `build_quantity_overbilled`: PO-2005, ordered 60, received 50."""
        po = purchase_order(po_number="PO-2005", quantity="60", unit_price="30.00")
        grn = goods_receipt(po_number="PO-2005", quantity="50", unit_price="30.00")
        inv = invoice(
            po_reference="PO-2005",
            lines=(invoice_line(quantity="55", unit_price="30.00", line_total="1650.00"),),
            subtotal="1650.00",
            tax_amount=None,
            total_amount="1650.00",
        )
        result = three_way_match(context(inv=inv, po=po, grn=grn))
        assert result.verdict is Verdict.FAIL
        assert "Billed 55" in result.reasoning
        assert "50 received" in result.reasoning

    def test_overbilling_allowed_by_pack_does_not_fail_for_that_reason(self) -> None:
        pack = policy_pack(
            tolerances=policy_pack().tolerances.model_copy(update={"allow_overbilling": True})
        )
        po = purchase_order(po_number="PO-2005", quantity="60", unit_price="30.00")
        grn = goods_receipt(po_number="PO-2005", quantity="50", unit_price="30.00")
        inv = invoice(
            po_reference="PO-2005",
            lines=(invoice_line(quantity="55", unit_price="30.00", line_total="1650.00"),),
            subtotal="1650.00",
            tax_amount=None,
            total_amount="1650.00",
        )
        result = three_way_match(context(inv=inv, policy=pack, po=po, grn=grn))
        assert result.verdict is Verdict.PASS


class TestPriceVariance:
    def test_price_within_tolerance_passes(self) -> None:
        """Mirrors `build_price_variance_within_tolerance`: PO-2007 @ 20.00,
        invoice bumped by <2%."""
        po = purchase_order(po_number="PO-2007", quantity="100", unit_price="20.00")
        grn = goods_receipt(po_number="PO-2007", quantity="100", unit_price="20.00")
        bumped_price = Decimal("20.00") * Decimal("1.010")  # 1.0% bump
        line_total = (bumped_price * Decimal("40")).quantize(Decimal("0.01"))
        inv = invoice(
            po_reference="PO-2007",
            lines=(
                invoice_line(
                    quantity="40",
                    unit_price=str(bumped_price.quantize(Decimal("0.01"))),
                    line_total=str(line_total),
                ),
            ),
            subtotal=str(line_total),
            tax_amount=None,
            total_amount=str(line_total),
        )
        result = three_way_match(context(inv=inv, po=po, grn=grn))
        assert result.verdict is Verdict.PASS

    def test_price_exceeding_tolerance_fails(self) -> None:
        """Mirrors `build_price_variance_exceeds_tolerance`: PO-2008 @ 10.00,
        invoice bumped by > 2%."""
        po = purchase_order(po_number="PO-2008", quantity="200", unit_price="10.00")
        grn = goods_receipt(po_number="PO-2008", quantity="200", unit_price="10.00")
        bumped_price = Decimal("10.80")  # 8% bump
        line_total = (bumped_price * Decimal("60")).quantize(Decimal("0.01"))
        inv = invoice(
            po_reference="PO-2008",
            lines=(
                invoice_line(
                    quantity="60", unit_price=str(bumped_price), line_total=str(line_total)
                ),
            ),
            subtotal=str(line_total),
            tax_amount=None,
            total_amount=str(line_total),
        )
        result = three_way_match(context(inv=inv, po=po, grn=grn))
        assert result.verdict is Verdict.FAIL
        assert "Price variance" in result.reasoning


class TestPartialDelivery:
    def test_billing_the_partially_received_quantity_passes(self) -> None:
        """Mirrors `build_partial_delivery`: PO-2003, ordered 100, received 60."""
        po = purchase_order(po_number="PO-2003", quantity="100", unit_price="50.00")
        grn = goods_receipt(po_number="PO-2003", quantity="60", unit_price="50.00", is_partial=True)
        inv = invoice(
            po_reference="PO-2003",
            lines=(invoice_line(quantity="55", unit_price="50.00", line_total="2750.00"),),
            subtotal="2750.00",
            tax_amount=None,
            total_amount="2750.00",
        )
        result = three_way_match(context(inv=inv, po=po, grn=grn))
        assert result.verdict is Verdict.PASS
        assert "reconciles" in result.reasoning

    def test_billing_more_than_the_partial_receipt_fails(self) -> None:
        po = purchase_order(po_number="PO-2003", quantity="100", unit_price="50.00")
        grn = goods_receipt(po_number="PO-2003", quantity="60", unit_price="50.00", is_partial=True)
        inv = invoice(
            po_reference="PO-2003",
            lines=(invoice_line(quantity="70", unit_price="50.00", line_total="3500.00"),),
            subtotal="3500.00",
            tax_amount=None,
            total_amount="3500.00",
        )
        result = three_way_match(context(inv=inv, po=po, grn=grn))
        assert result.verdict is Verdict.FAIL

    def test_partial_delivery_forbidden_by_pack_fails(self) -> None:
        pack = policy_pack(
            tolerances=policy_pack().tolerances.model_copy(
                update={"allow_partial_delivery": False}
            )
        )
        po = purchase_order(po_number="PO-2003", quantity="100", unit_price="50.00")
        grn = goods_receipt(po_number="PO-2003", quantity="60", unit_price="50.00", is_partial=True)
        inv = invoice(
            po_reference="PO-2003",
            lines=(invoice_line(quantity="55", unit_price="50.00", line_total="2750.00"),),
            subtotal="2750.00",
            tax_amount=None,
            total_amount="2750.00",
        )
        result = three_way_match(context(inv=inv, policy=pack, po=po, grn=grn))
        assert result.verdict is Verdict.FAIL
        assert "does not permit partial delivery" in result.reasoning


class TestCleanMatch:
    def test_exact_quantity_and_price_match_passes(self) -> None:
        """Mirrors `build_clean_touchless`: PO-2001, fully matched."""
        po = purchase_order(po_number="PO-2001", quantity="120", unit_price="12.00")
        grn = goods_receipt(po_number="PO-2001", quantity="120", unit_price="12.00")
        inv = invoice(
            po_reference="PO-2001",
            lines=(invoice_line(quantity="20", unit_price="12.00", line_total="240.00"),),
            subtotal="240.00",
            tax_amount="19.80",
            total_amount="259.80",
        )
        result = three_way_match(context(inv=inv, po=po, grn=grn))
        assert result.verdict is Verdict.PASS


class TestCurrencyMismatchAgainstPo:
    def test_invoice_line_currency_differing_from_po_fails(self) -> None:
        po = purchase_order(po_number="PO-2001", quantity="120", unit_price="12.00", currency="USD")
        grn = goods_receipt(po_number="PO-2001", quantity="120", unit_price="12.00", currency="USD")
        inv = invoice(
            po_reference="PO-2001",
            currency="EUR",
            lines=(
                invoice_line(
                    quantity="20", unit_price="12.00", line_total="240.00", currency="EUR"
                ),
            ),
            subtotal="240.00",
            tax_amount="19.80",
            total_amount="259.80",
        )
        result = three_way_match(context(inv=inv, po=po, grn=grn))
        assert result.verdict is Verdict.FAIL
        assert "currency" in result.reasoning.lower()
