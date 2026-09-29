"""`vendor_resolution`, `vendor_active`, `bank_detail_change`, `new_vendor`
(FR-4.5, FR-4.6, FR-4.8 pairing signal)."""

from __future__ import annotations

from datetime import date, datetime

from ap_agent.core.canonical import VendorStatus
from ap_agent.core.checks import Verdict
from ap_agent.mapping.vendors import ResolutionOutcome, VendorCandidate, VendorResolution
from ap_agent.policy.identity import (
    BANK_DETAIL_CHANGE,
    NEW_VENDOR,
    VENDOR_ACTIVE,
    VENDOR_RESOLUTION,
    bank_detail_change,
    new_vendor,
    vendor_active,
    vendor_resolution,
)
from tests.unit.policy_fixtures import context, invoice, resolved, vendor


class TestVendorResolution:
    def test_no_resolution_supplied_skips(self) -> None:
        result = vendor_resolution(context())
        assert result.verdict is Verdict.SKIP
        assert result.name == VENDOR_RESOLUTION

    def test_resolved_outcome_passes(self) -> None:
        result = vendor_resolution(context(vendor_res=resolved()))
        assert result.verdict is Verdict.PASS

    def test_alias_match_outcome_passes(self) -> None:
        res = VendorResolution(
            printed_name="ACME", outcome=ResolutionOutcome.ALIAS_MATCH, vendor_id="V-1",
        )
        result = vendor_resolution(context(vendor_res=res))
        assert result.verdict is Verdict.PASS

    def test_ambiguous_outcome_flags(self) -> None:
        res = VendorResolution(
            printed_name="Acme Corp",
            outcome=ResolutionOutcome.AMBIGUOUS,
            candidates=(
                VendorCandidate(vendor_id="V-1001", legal_name="Acme Corporation", score=0.93),
                VendorCandidate(vendor_id="V-1002", legal_name="Acme Industries LLC", score=0.91),
            ),
        )
        result = vendor_resolution(context(vendor_res=res))
        assert result.verdict is Verdict.FLAG
        assert result.forces_review is True
        assert result.inputs["candidates"] == ["V-1001", "V-1002"]

    def test_not_found_outcome_flags(self) -> None:
        res = VendorResolution(printed_name="Nobody Inc", outcome=ResolutionOutcome.NOT_FOUND)
        result = vendor_resolution(context(vendor_res=res))
        assert result.verdict is Verdict.FLAG
        assert result.forces_review is True


class TestVendorActive:
    def test_no_vendor_skips(self) -> None:
        result = vendor_active(context())
        assert result.verdict is Verdict.SKIP
        assert result.name == VENDOR_ACTIVE

    def test_active_vendor_passes(self) -> None:
        result = vendor_active(context(ven=vendor(status=VendorStatus.ACTIVE)))
        assert result.verdict is Verdict.PASS

    def test_blocked_vendor_fails_critically(self) -> None:
        result = vendor_active(context(ven=vendor(status=VendorStatus.BLOCKED)))
        assert result.verdict is Verdict.FAIL
        assert result.severity.value == "critical"
        assert result.forces_review is True
        assert "blocked" in result.reasoning

    def test_inactive_vendor_fails_but_not_critically(self) -> None:
        result = vendor_active(context(ven=vendor(status=VendorStatus.INACTIVE)))
        assert result.verdict is Verdict.FAIL
        assert result.severity.value != "critical"
        assert "inactive" in result.reasoning


class TestBankDetailChange:
    def test_no_remit_to_on_invoice_skips(self) -> None:
        inv = invoice(remit_to_last4=None, remit_to_bank_name=None)
        result = bank_detail_change(context(inv=inv, ven=vendor()))
        assert result.verdict is Verdict.SKIP
        assert result.name == BANK_DETAIL_CHANGE

    def test_no_master_record_skips(self) -> None:
        result = bank_detail_change(context(inv=invoice(remit_to_last4="4821")))
        assert result.verdict is Verdict.SKIP

    def test_matching_fingerprint_passes(self) -> None:
        inv = invoice(remit_to_last4="4821")
        result = bank_detail_change(context(inv=inv, ven=vendor(bank_last4="4821")))
        assert result.verdict is Verdict.PASS

    def test_changed_last4_flags_regardless_of_amount(self) -> None:
        inv = invoice(remit_to_last4="7000", total_amount="1.00", subtotal="1.00", tax_amount=None)
        result = bank_detail_change(context(inv=inv, ven=vendor(bank_last4="4821")))
        assert result.verdict is Verdict.FLAG
        assert result.severity.value == "critical"
        assert result.forces_review is True

    def test_changed_bank_name_with_same_last4_still_flags(self) -> None:
        # fingerprint() includes bank_name, so a redirect to a different bank
        # under the same last four digits (unlikely but not impossible) must
        # still be caught.
        inv = invoice(remit_to_last4="4821", remit_to_bank_name="Shady Offshore Bank")
        result = bank_detail_change(
            context(inv=inv, ven=vendor(bank_last4="4821", bank_name="First National"))
        )
        assert result.verdict is Verdict.FLAG


class TestNewVendor:
    def test_no_vendor_skips(self) -> None:
        result = new_vendor(context())
        assert result.verdict is Verdict.SKIP
        assert result.name == NEW_VENDOR

    def test_no_first_seen_date_skips(self) -> None:
        result = new_vendor(context(ven=vendor(first_seen_at=None)))
        assert result.verdict is Verdict.SKIP

    def test_established_vendor_passes(self) -> None:
        inv = invoice(invoice_date_=date(2026, 2, 27))
        result = new_vendor(
            context(inv=inv, ven=vendor(first_seen_at=datetime(2020, 1, 1)))
        )
        assert result.verdict is Verdict.PASS

    def test_recently_created_vendor_flags(self) -> None:
        inv = invoice(invoice_date_=date(2026, 2, 27))
        result = new_vendor(
            context(inv=inv, ven=vendor(first_seen_at=datetime(2026, 2, 20)))
        )
        assert result.verdict is Verdict.FLAG
        assert result.forces_review is True
        assert result.inputs["age_days"] == 7

    def test_boundary_at_exactly_the_configured_window_passes(self) -> None:
        inv = invoice(invoice_date_=date(2026, 2, 27))
        # Exactly 30 days before, and the default window is 30 days
        # (>= 30 passes per the check's `age_days < band` condition).
        result = new_vendor(
            context(inv=inv, ven=vendor(first_seen_at=datetime(2026, 1, 28)))
        )
        assert result.verdict is Verdict.PASS
