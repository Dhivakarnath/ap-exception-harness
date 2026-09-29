"""`math_integrity` and `currency_consistency` (FR-4.7).

Money is Decimal end to end here on purpose: a float-based check would
produce spurious findings on legitimate invoices and hide real ones, which is
exactly the property these tests pin.
"""

from __future__ import annotations

from decimal import Decimal

from ap_agent.core.checks import Verdict
from ap_agent.core.policy_pack import Tolerances
from ap_agent.policy.arithmetic import (
    CURRENCY_CONSISTENCY,
    MATH_INTEGRITY,
    currency_consistency,
    math_integrity,
)
from tests.unit.policy_fixtures import extracted, invoice, invoice_line, money

_TOLERANCES = Tolerances(total_absolute=Decimal("0.01"))


class TestMathIntegrity:
    def test_reconciling_invoice_passes(self) -> None:
        result = math_integrity(invoice(), _TOLERANCES)
        assert result.verdict is Verdict.PASS
        assert result.name == MATH_INTEGRITY

    def test_line_sum_not_matching_subtotal_fails(self) -> None:
        # Lines sum to 240.00 (20 x 12.00) but the printed subtotal claims 245.00.
        inv = invoice(
            subtotal="245.00",
            tax_amount="19.80",
            total_amount="264.80",
            lines=(invoice_line(quantity="20", unit_price="12.00", line_total="240.00"),),
        )
        result = math_integrity(inv, _TOLERANCES)
        assert result.verdict is Verdict.FAIL
        assert result.forces_review is True
        assert "subtotal" in result.reasoning

    def test_subtotal_plus_tax_not_equal_total_fails(self) -> None:
        inv = invoice(subtotal="240.00", tax_amount="19.80", total_amount="300.00")
        result = math_integrity(inv, _TOLERANCES)
        assert result.verdict is Verdict.FAIL
        assert "total" in result.reasoning

    def test_no_tax_and_subtotal_equals_total_passes(self) -> None:
        inv = invoice(
            subtotal="240.00",
            tax_amount=None,
            total_amount="240.00",
            lines=(invoice_line(quantity="20", unit_price="12.00", line_total="240.00"),),
        )
        result = math_integrity(inv, _TOLERANCES)
        assert result.verdict is Verdict.PASS

    def test_header_only_invoice_with_no_lines_skips_line_sum_check(self) -> None:
        inv = invoice(lines=(), subtotal="240.00", tax_amount="19.80", total_amount="259.80")
        result = math_integrity(inv, _TOLERANCES)
        assert result.verdict is Verdict.PASS
        assert "computed_line_sum" not in result.inputs

    def test_rounding_drift_within_tolerance_passes(self) -> None:
        # 0.01 rounding drift on a subtotal-plus-tax check must not fail when
        # the configured tolerance is 0.01.
        inv = invoice(subtotal="240.00", tax_amount="19.80", total_amount="259.81")
        result = math_integrity(inv, _TOLERANCES)
        assert result.verdict is Verdict.PASS

    def test_drift_beyond_tolerance_fails(self) -> None:
        inv = invoice(subtotal="240.00", tax_amount="19.80", total_amount="259.82")
        result = math_integrity(inv, _TOLERANCES)
        assert result.verdict is Verdict.FAIL

    def test_tolerance_is_the_tenants_configured_value_not_a_fixed_constant(self) -> None:
        # Manufacturing configures 1.00; retail configures 5.00 (see the
        # committed policy packs). The same drift must pass under a looser
        # tolerance and fail under a tighter one.
        inv = invoice(subtotal="240.00", tax_amount="19.80", total_amount="262.00")  # 2.20 off
        loose = math_integrity(inv, Tolerances(total_absolute=Decimal("5.00")))
        tight = math_integrity(inv, Tolerances(total_absolute=Decimal("1.00")))
        assert loose.verdict is Verdict.PASS
        assert tight.verdict is Verdict.FAIL

    def test_both_reconciliations_reported_together_when_both_fail(self) -> None:
        inv = invoice(
            subtotal="245.00",
            tax_amount="19.80",
            total_amount="300.00",
            lines=(invoice_line(quantity="20", unit_price="12.00", line_total="240.00"),),
        )
        result = math_integrity(inv, _TOLERANCES)
        assert result.verdict is Verdict.FAIL
        assert "subtotal" in result.reasoning
        assert "total" in result.reasoning


class TestCurrencyConsistency:
    def test_single_currency_passes(self) -> None:
        result = currency_consistency(invoice())
        assert result.verdict is Verdict.PASS
        assert result.name == CURRENCY_CONSISTENCY
        assert result.actual == "USD"

    def test_mixed_currency_line_fails(self) -> None:
        mixed_line = invoice_line(currency="EUR")
        inv = invoice(currency="USD", lines=(mixed_line,))
        result = currency_consistency(inv)
        assert result.verdict is Verdict.FAIL
        assert result.forces_review is True
        assert "EUR" in result.actual
        assert "USD" in result.actual

    def test_mixed_currency_tax_fails(self) -> None:
        inv = invoice(currency="USD").model_copy(
            update={"tax_amount": extracted(money("19.80", "GBP"))}
        )
        result = currency_consistency(inv)
        assert result.verdict is Verdict.FAIL
