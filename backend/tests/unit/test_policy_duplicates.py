"""`duplicate_exact` and `duplicate_fuzzy` (FR-4.3).

Mirrors the adversarial fixture set's own duplicate cases: an exact
resubmission (`build_exact_duplicate`) and a suffixed near-miss
(`build_fuzzy_duplicate`) both from `apfixtures.cases`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ap_agent.core.checks import Verdict
from ap_agent.policy.duplicates import (
    DUPLICATE_EXACT,
    DUPLICATE_FUZZY,
    duplicate_exact,
    duplicate_fuzzy,
)
from tests.unit.policy_fixtures import context, historical_bill, invoice, policy_pack, vendor


class TestDuplicateExact:
    def test_no_history_passes(self) -> None:
        result = duplicate_exact(context())
        assert result.verdict is Verdict.PASS
        assert result.name == DUPLICATE_EXACT

    def test_identical_vendor_number_amount_fails(self) -> None:
        inv = invoice(invoice_number="INV-77001", total_amount="1500.00", tax_amount=None, subtotal="1500.00")
        bill = historical_bill(invoice_number="INV-77001", amount="1500.00")
        result = duplicate_exact(context(inv=inv, ven=vendor(), bills=(bill,)))
        assert result.verdict is Verdict.FAIL
        assert result.forces_review is True
        assert result.severity.value == "critical"

    def test_case_and_whitespace_insensitive_number_match(self) -> None:
        inv = invoice(invoice_number=" inv-77001 ", total_amount="1500.00", tax_amount=None, subtotal="1500.00")
        bill = historical_bill(invoice_number="INV-77001", amount="1500.00")
        result = duplicate_exact(context(inv=inv, ven=vendor(), bills=(bill,)))
        assert result.verdict is Verdict.FAIL

    def test_different_amount_does_not_match(self) -> None:
        inv = invoice(invoice_number="INV-77001", total_amount="1501.00", tax_amount=None, subtotal="1501.00")
        bill = historical_bill(invoice_number="INV-77001", amount="1500.00")
        result = duplicate_exact(context(inv=inv, ven=vendor(), bills=(bill,)))
        assert result.verdict is Verdict.PASS

    def test_different_vendor_does_not_match(self) -> None:
        inv = invoice(invoice_number="INV-77001", total_amount="1500.00", tax_amount=None, subtotal="1500.00")
        bill = historical_bill(invoice_number="INV-77001", amount="1500.00", vendor_id="V-OTHER")
        result = duplicate_exact(context(inv=inv, ven=vendor(), bills=(bill,)))
        assert result.verdict is Verdict.PASS

    def test_outside_lookback_window_does_not_match(self) -> None:
        pack = policy_pack(duplicates=policy_pack().duplicates.model_copy(update={"lookback_days": 10}))
        inv = invoice(
            invoice_number="INV-77001",
            total_amount="1500.00",
            tax_amount=None,
            subtotal="1500.00",
            invoice_date_=date(2026, 3, 1),
        )
        bill = historical_bill(invoice_number="INV-77001", amount="1500.00", invoice_date_=date(2026, 1, 1))
        result = duplicate_exact(context(inv=inv, policy=pack, ven=vendor(), bills=(bill,)))
        assert result.verdict is Verdict.PASS

    def test_future_dated_history_does_not_match(self) -> None:
        # A "historical" bill dated after the invoice is not a valid duplicate
        # comparison — the lookback window looks backward only.
        inv = invoice(
            invoice_number="INV-77001",
            total_amount="1500.00",
            tax_amount=None,
            subtotal="1500.00",
            invoice_date_=date(2026, 1, 1),
        )
        bill = historical_bill(invoice_number="INV-77001", amount="1500.00", invoice_date_=date(2026, 3, 1))
        result = duplicate_exact(context(inv=inv, ven=vendor(), bills=(bill,)))
        assert result.verdict is Verdict.PASS

    def test_name_keyed_match_when_vendor_unresolved(self) -> None:
        inv = invoice(
            invoice_number="INV-77001",
            total_amount="1500.00",
            tax_amount=None,
            subtotal="1500.00",
            vendor_name="Acme Corporation",
        )
        bill = historical_bill(
            invoice_number="INV-77001", amount="1500.00", vendor_id="V-UNUSED",
            vendor_name="Acme Corporation",
        )
        # No `ven=` supplied and the invoice carries no resolved_vendor_id, so
        # the name-keyed fallback path is what must catch this.
        result = duplicate_exact(context(inv=inv, bills=(bill,)))
        assert result.verdict is Verdict.FAIL


class TestDuplicateFuzzy:
    def test_no_history_passes(self) -> None:
        result = duplicate_fuzzy(context())
        assert result.verdict is Verdict.PASS
        assert result.name == DUPLICATE_FUZZY

    def test_suffixed_number_same_amount_nearby_date_flags(self) -> None:
        inv = invoice(
            invoice_number="INV-88001A",
            total_amount="2750.00",
            tax_amount=None,
            subtotal="2750.00",
            invoice_date_=date(2026, 2, 5),
        )
        bill = historical_bill(
            invoice_number="INV-88001", amount="2750.00", invoice_date_=date(2026, 2, 3)
        )
        result = duplicate_fuzzy(context(inv=inv, ven=vendor(), bills=(bill,)))
        assert result.verdict is Verdict.FLAG
        assert result.forces_review is True

    def test_identical_number_is_not_a_fuzzy_match(self) -> None:
        # An identical number belongs to duplicate_exact's territory, not
        # fuzzy's — fuzzy must not double-flag it.
        inv = invoice(
            invoice_number="INV-88001",
            total_amount="2750.00",
            tax_amount=None,
            subtotal="2750.00",
            invoice_date_=date(2026, 2, 5),
        )
        bill = historical_bill(
            invoice_number="INV-88001", amount="2750.00", invoice_date_=date(2026, 2, 3)
        )
        result = duplicate_fuzzy(context(inv=inv, bills=(bill,)))
        assert result.verdict is Verdict.PASS

    def test_dissimilar_number_does_not_flag(self) -> None:
        inv = invoice(
            invoice_number="INV-99999",
            total_amount="2750.00",
            tax_amount=None,
            subtotal="2750.00",
            invoice_date_=date(2026, 2, 5),
        )
        bill = historical_bill(
            invoice_number="INV-88001", amount="2750.00", invoice_date_=date(2026, 2, 3)
        )
        result = duplicate_fuzzy(context(inv=inv, bills=(bill,)))
        assert result.verdict is Verdict.PASS

    def test_amount_outside_tolerance_does_not_flag(self) -> None:
        inv = invoice(
            invoice_number="INV-88001A",
            total_amount="3000.00",
            tax_amount=None,
            subtotal="3000.00",
            invoice_date_=date(2026, 2, 5),
        )
        bill = historical_bill(
            invoice_number="INV-88001", amount="2750.00", invoice_date_=date(2026, 2, 3)
        )
        result = duplicate_fuzzy(context(inv=inv, bills=(bill,)))
        assert result.verdict is Verdict.PASS

    def test_amount_within_configured_tolerance_still_flags(self) -> None:
        pack = policy_pack(
            duplicates=policy_pack().duplicates.model_copy(
                update={"fuzzy_amount_tolerance": Decimal("0.50")}
            )
        )
        inv = invoice(
            invoice_number="INV-88001A",
            total_amount="2750.40",
            tax_amount=None,
            subtotal="2750.40",
            invoice_date_=date(2026, 2, 5),
        )
        bill = historical_bill(
            invoice_number="INV-88001", amount="2750.00", invoice_date_=date(2026, 2, 3)
        )
        result = duplicate_fuzzy(context(inv=inv, policy=pack, ven=vendor(), bills=(bill,)))
        assert result.verdict is Verdict.FLAG

    def test_outside_date_window_does_not_flag(self) -> None:
        inv = invoice(
            invoice_number="INV-88001A",
            total_amount="2750.00",
            tax_amount=None,
            subtotal="2750.00",
            invoice_date_=date(2026, 3, 1),
        )
        bill = historical_bill(
            invoice_number="INV-88001", amount="2750.00", invoice_date_=date(2026, 2, 3)
        )
        result = duplicate_fuzzy(context(inv=inv, bills=(bill,)))
        assert result.verdict is Verdict.PASS
