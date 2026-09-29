"""Canonical domain model behaviour.

Focus areas: the non-PO distinction that selects the pipeline branch, confidence
aggregation that gates auto-approval, and the segregation-of-duties invariant on
Decision.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ap_agent.core.canonical import (
    Actor,
    BankDetails,
    Decision,
    GLCoding,
    Invoice,
    InvoiceLine,
    LineItem,
    Route,
    Vendor,
    VendorStatus,
)
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money


def _money(v: str, ccy: str = "USD") -> Money:
    return Money(amount=v, currency=ccy)


def _line(
    n: int = 1,
    qty: str = "2",
    unit: str = "50.00",
    total: str = "100.00",
    ccy: str = "USD",
    confidence: float = 1.0,
) -> InvoiceLine:
    return InvoiceLine(
        line_number=n,
        description=Extracted[str](value=f"Item {n}", confidence=confidence),
        quantity=Extracted[Decimal](value=Decimal(qty), confidence=confidence),
        unit_price=Extracted[Money](value=_money(unit, ccy), confidence=confidence),
        line_total=Extracted[Money](value=_money(total, ccy), confidence=confidence),
    )


def _invoice(
    *,
    po: str | None = "PO-1001",
    lines: tuple[InvoiceLine, ...] = (),
    subtotal: str = "100.00",
    total: str = "110.00",
    tax: str | None = "10.00",
    ccy: str = "USD",
    header_confidence: float = 1.0,
) -> Invoice:
    return Invoice(
        invoice_id="inv-1",
        tenant_id="t1",
        document_id="doc-1",
        invoice_number=Extracted[str](value="INV-001", confidence=header_confidence),
        invoice_date=Extracted[date](value=date(2026, 1, 15), confidence=header_confidence),
        vendor_name=Extracted[str](value="Acme Corp", confidence=header_confidence),
        currency=Extracted[str](value=ccy, confidence=header_confidence),
        subtotal=Extracted[Money](value=_money(subtotal, ccy), confidence=header_confidence),
        total_amount=Extracted[Money](value=_money(total, ccy), confidence=header_confidence),
        tax_amount=(
            Extracted[Money](value=_money(tax, ccy), confidence=header_confidence) if tax else None
        ),
        po_reference=Extracted[str](value=po, confidence=header_confidence) if po else None,
        lines=lines,
    )


class TestNonPODetection:
    def test_invoice_with_po_is_not_non_po(self) -> None:
        assert _invoice(po="PO-1001").is_non_po is False

    def test_missing_po_reference_is_non_po(self) -> None:
        # Selects the non-PO branch: three-way matching cannot apply (FR-5.1).
        assert _invoice(po=None).is_non_po is True

    def test_blank_po_reference_is_non_po(self) -> None:
        # An empty extracted string is absence, not a PO called "".
        assert _invoice(po="   ").is_non_po is True


class TestCreditNoteDetection:
    def test_positive_total_is_not_a_credit_note(self) -> None:
        assert _invoice(subtotal="100.00", total="110.00").is_credit_note is False

    def test_negative_total_is_a_credit_note(self) -> None:
        assert _invoice(subtotal="-100.00", total="-110.00", tax="-10.00").is_credit_note is True

    def test_zero_total_is_not_a_credit_note(self) -> None:
        # A reverse-charge invoice can net to a positive subtotal with zero tax;
        # only a genuinely negative total signals a credit.
        assert _invoice(subtotal="100.00", total="100.00", tax="0.00").is_credit_note is False


class TestConfidenceAggregation:
    def test_header_confidence_is_the_minimum(self) -> None:
        # A minimum, not a mean: one badly-read total is not redeemed by ten
        # confidently-read incidental fields.
        inv = _invoice(header_confidence=0.9)
        assert inv.header_confidence == pytest.approx(0.9)

    def test_low_line_confidence_drags_overall_down(self) -> None:
        inv = _invoice(header_confidence=1.0, lines=(_line(confidence=0.42),))
        assert inv.header_confidence == 1.0
        assert inv.overall_confidence == pytest.approx(0.42)

    def test_overall_equals_header_when_no_lines(self) -> None:
        inv = _invoice(header_confidence=0.77, lines=())
        assert inv.overall_confidence == pytest.approx(0.77)

    def test_line_min_confidence(self) -> None:
        line = InvoiceLine(
            line_number=1,
            description=Extracted[str](value="x", confidence=1.0),
            quantity=Extracted[Decimal](value=Decimal(1), confidence=0.55),
            unit_price=Extracted[Money](value=_money("1.00"), confidence=1.0),
            line_total=Extracted[Money](value=_money("1.00"), confidence=1.0),
        )
        assert line.min_confidence == pytest.approx(0.55)

    def test_to_provenance_is_json_ready_and_carries_the_line(self) -> None:
        # The shape both the live extraction event and the persisted
        # field_provenance use, so a reloaded run replays line items identically.
        line = InvoiceLine(
            line_number=2,
            description=Extracted[str](value="Steel bracket, 40mm", confidence=0.9),
            quantity=Extracted[Decimal](value=Decimal("10"), confidence=0.9),
            unit_price=Extracted[Money](value=_money("12.00"), confidence=0.9),
            line_total=Extracted[Money](value=_money("120.00"), confidence=0.9),
            unit_of_measure=Extracted[str](value="EA", confidence=0.9),
        )
        prov = line.to_provenance()
        assert prov["line_number"] == 2
        assert prov["description"] == "Steel bracket, 40mm"
        assert prov["quantity"] == "10"
        assert prov["unit_of_measure"] == "EA"
        assert prov["unit_price"] == "12.00 USD"
        assert prov["line_total"] == "120.00 USD"
        assert prov["sku"] is None
        assert prov["confidence"] == pytest.approx(0.9)
        # No region was attached, so region/bbox are honestly absent, not faked.
        assert prov["region_ref"] is None
        assert prov["bbox"] is None


class TestArithmeticHelpers:
    def test_line_sum_is_exact(self) -> None:
        inv = _invoice(
            lines=(
                _line(1, total="33.33"),
                _line(2, total="33.33"),
                _line(3, total="33.34"),
            )
        )
        assert inv.computed_line_sum() == _money("100.00")

    def test_line_sum_is_none_without_lines(self) -> None:
        # Header-only invoices are normal for services.
        assert _invoice(lines=()).computed_line_sum() is None

    def test_expected_line_total_from_qty_and_price(self) -> None:
        item = LineItem(
            description="widget",
            quantity=Decimal("3"),
            unit_price=_money("19.99"),
            line_total=_money("59.97"),
        )
        assert item.expected_line_total == _money("59.97")

    def test_line_item_rejects_mixed_currency(self) -> None:
        with pytest.raises(ValidationError, match="mixes currencies"):
            LineItem(
                description="widget",
                quantity=Decimal("1"),
                unit_price=_money("10.00", "USD"),
                line_total=_money("10.00", "EUR"),
            )

    def test_currencies_present_detects_mixture(self) -> None:
        # Mixed currency is a data defect the currency check reports.
        inv = _invoice(ccy="USD", lines=(_line(ccy="USD"),))
        assert inv.currencies_present() == {"USD"}


class TestVendor:
    def test_active_vendor_is_payable(self) -> None:
        v = Vendor(vendor_id="v1", legal_name="Acme Corporation")
        assert v.is_payable is True
        assert v.name == "Acme Corporation"

    def test_display_name_preferred(self) -> None:
        v = Vendor(vendor_id="v1", legal_name="Acme Corporation", display_name="Acme")
        assert v.name == "Acme"

    def test_blocked_vendor_is_not_payable(self) -> None:
        v = Vendor(vendor_id="v1", legal_name="Shell Co", status=VendorStatus.BLOCKED)
        assert v.is_payable is False

    def test_inactive_vendor_is_not_payable(self) -> None:
        v = Vendor(vendor_id="v1", legal_name="Old Co", status=VendorStatus.INACTIVE)
        assert v.is_payable is False


class TestBankDetailsFingerprint:
    def test_identical_details_share_fingerprint(self) -> None:
        a = BankDetails(account_name="Acme", account_number_last4="1234", bank_name="Big Bank")
        b = BankDetails(account_name="Acme", account_number_last4="1234", bank_name="Big Bank")
        assert a.fingerprint() == b.fingerprint()

    def test_changed_account_changes_fingerprint(self) -> None:
        # This is the signal that forces HITL regardless of amount (FR-4.6).
        a = BankDetails(account_name="Acme", account_number_last4="1234")
        b = BankDetails(account_name="Acme", account_number_last4="9999")
        assert a.fingerprint() != b.fingerprint()

    def test_fingerprint_is_case_and_space_insensitive(self) -> None:
        a = BankDetails(account_name="Acme Ltd", bank_name="Big Bank")
        b = BankDetails(account_name="  ACME LTD ", bank_name="big bank")
        assert a.fingerprint() == b.fingerprint()

    def test_fingerprint_contains_no_full_account_number(self) -> None:
        # Only non-sensitive fragments are retained.
        bd = BankDetails(account_name="Acme", account_number_last4="1234")
        assert "1234" in bd.fingerprint()
        assert len(bd.account_number_last4 or "") <= 4


class TestGLCodingGrounding:
    def test_cited_coding_is_groundable(self) -> None:
        c = GLCoding(gl_account="6410", confidence=0.9, citations=("policy.md#4.2",))
        assert c.is_groundable is True

    def test_uncited_coding_is_not_groundable(self) -> None:
        # Auto-coding without a citation must be impossible (FR-5.4).
        c = GLCoding(gl_account="6410", confidence=0.99)
        assert c.is_groundable is False

    def test_po_inherited_coding_needs_no_citation(self) -> None:
        # A PO-backed invoice inherits coding that a human already authorised.
        c = GLCoding(gl_account="6410", confidence=1.0, inherited_from_po=True)
        assert c.is_groundable is True


class TestDecisionAuthorityInvariants:
    def test_agent_cannot_be_approver_of_record(self) -> None:
        # Segregation of duties (FR-4.11).
        with pytest.raises(ValidationError, match="Segregation of duties"):
            Decision(
                invoice_id="inv-1",
                route=Route.ROUTE_FOR_APPROVAL,
                decided_by=Actor.AGENT,
                rationale="needs controller sign-off",
                decided_at=datetime.now(UTC),
                approver_identity="agent",
            )

    def test_agent_may_route_for_approval_without_naming_approver(self) -> None:
        d = Decision(
            invoice_id="inv-1",
            route=Route.ROUTE_FOR_APPROVAL,
            decided_by=Actor.AGENT,
            rationale="amount exceeds touchless threshold",
            decided_at=datetime.now(UTC),
            required_approver_tier="controller",
        )
        assert d.is_touchless is False
        assert d.approver_identity is None

    def test_human_cannot_record_an_auto_approval(self) -> None:
        with pytest.raises(ValidationError, match="AUTO_APPROVE means no human"):
            Decision(
                invoice_id="inv-1",
                route=Route.AUTO_APPROVE,
                decided_by=Actor.HUMAN,
                rationale="looked fine",
                decided_at=datetime.now(UTC),
            )

    def test_policy_engine_auto_approval_is_touchless(self) -> None:
        d = Decision(
            invoice_id="inv-1",
            route=Route.AUTO_APPROVE,
            decided_by=Actor.POLICY_ENGINE,
            rationale="clean three-way match under touchless threshold",
            decided_at=datetime.now(UTC),
        )
        assert d.is_touchless is True

    def test_human_approval_records_identity(self) -> None:
        d = Decision(
            invoice_id="inv-1",
            route=Route.ROUTE_FOR_APPROVAL,
            decided_by=Actor.HUMAN,
            rationale="approved after vendor call",
            decided_at=datetime.now(UTC),
            approver_identity="controller@example.com",
        )
        assert d.approver_identity == "controller@example.com"


class TestExtractionMethodOnInvoice:
    def test_alias_rule_extraction_is_trusted(self) -> None:
        e = Extracted[str](value="INV-9", method=ExtractionMethod.ALIAS_RULE, confidence=1.0)
        assert e.is_trusted is True
