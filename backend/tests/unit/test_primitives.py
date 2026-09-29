"""Money and provenance correctness.

The money tests matter more than they look: this system authorises payments, and
every downstream arithmetic check (FR-4.7) is only as trustworthy as the type it
operates on.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from ap_agent.core.primitives import (
    BoundingBox,
    CurrencyError,
    Extracted,
    ExtractionMethod,
    Money,
    RegionKind,
    SourceRegion,
    minor_units,
    quantise,
)


class TestMoneyRejectsFloat:
    def test_float_amount_is_rejected(self) -> None:
        # The whole point: float would import binary rounding error into
        # payment arithmetic.
        with pytest.raises(ValidationError, match="must not be a float"):
            Money(amount=10.5, currency="USD")  # type: ignore[arg-type]

    def test_multiplying_by_float_is_rejected(self) -> None:
        m = Money(amount="10.00", currency="USD")
        with pytest.raises(TypeError, match="Refusing to multiply"):
            m * 1.5  # type: ignore[operator]

    def test_string_and_int_and_decimal_are_accepted(self) -> None:
        assert Money(amount="10.50", currency="USD").amount == Decimal("10.50")
        assert Money(amount=10, currency="USD").amount == Decimal("10.00")
        assert Money(amount=Decimal("10.505"), currency="USD").amount == Decimal("10.51")


class TestExactArithmetic:
    def test_classic_float_trap_is_exact_here(self) -> None:
        # 0.1 + 0.2 != 0.3 in binary float. It must be exact in Money.
        a = Money(amount="0.10", currency="USD")
        b = Money(amount="0.20", currency="USD")
        assert (a + b).amount == Decimal("0.30")

    def test_summing_many_lines_does_not_drift(self) -> None:
        total = Money.zero("USD")
        for _ in range(1000):
            total = total + Money(amount="0.01", currency="USD")
        assert total.amount == Decimal("10.00")

    def test_subtraction_and_abs_difference(self) -> None:
        a = Money(amount="100.00", currency="USD")
        b = Money(amount="103.50", currency="USD")
        assert (b - a).amount == Decimal("3.50")
        assert a.abs_difference(b).amount == Decimal("3.50")
        assert b.abs_difference(a).amount == Decimal("3.50")


class TestCurrencySafety:
    def test_mixing_currencies_raises_rather_than_converting(self) -> None:
        usd = Money(amount="10.00", currency="USD")
        eur = Money(amount="10.00", currency="EUR")
        with pytest.raises(CurrencyError, match="Implicit FX conversion"):
            usd + eur

    def test_comparison_across_currencies_raises(self) -> None:
        usd = Money(amount="1.00", currency="USD")
        gbp = Money(amount="2.00", currency="GBP")
        with pytest.raises(CurrencyError):
            _ = usd < gbp

    def test_unsupported_currency_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Money(amount="1.00", currency="XYZ")

    def test_currency_code_is_normalised(self) -> None:
        assert Money(amount="1.00", currency="usd").currency == "USD"


class TestMinorUnits:
    def test_zero_decimal_currency(self) -> None:
        # JPY has no minor unit; assuming 2 places would be wrong.
        assert minor_units("JPY") == 0
        assert Money(amount="1234.56", currency="JPY").amount == Decimal("1235")

    def test_three_decimal_currency(self) -> None:
        assert minor_units("KWD") == 3
        assert Money(amount="1.2345", currency="KWD").amount == Decimal("1.235")

    def test_unsupported_currency_refuses_to_guess(self) -> None:
        with pytest.raises(CurrencyError, match="Refusing to guess"):
            minor_units("ZZZ")

    def test_half_up_rounding(self) -> None:
        # Conventional invoice rounding, not banker's rounding.
        assert quantise(Decimal("2.005"), "USD") == Decimal("2.01")
        assert quantise(Decimal("2.015"), "USD") == Decimal("2.02")


class TestVariance:
    def test_percentage_variance(self) -> None:
        actual = Money(amount="102.00", currency="USD")
        baseline = Money(amount="100.00", currency="USD")
        assert actual.variance_pct_against(baseline) == Decimal("2")

    def test_negative_variance_is_signed(self) -> None:
        actual = Money(amount="95.00", currency="USD")
        baseline = Money(amount="100.00", currency="USD")
        assert actual.variance_pct_against(baseline) == Decimal("-5")

    def test_zero_baseline_with_nonzero_actual_is_infinite(self) -> None:
        # Unbounded rather than an exception, so tolerance rules can simply fail
        # it instead of the pipeline crashing on a bad PO.
        v = Money(amount="10.00", currency="USD").variance_pct_against(Money.zero("USD"))
        assert v == Decimal("Infinity")

    def test_zero_baseline_with_zero_actual_is_zero(self) -> None:
        v = Money.zero("USD").variance_pct_against(Money.zero("USD"))
        assert v == Decimal(0)


class TestImmutability:
    def test_money_is_frozen(self) -> None:
        m = Money(amount="1.00", currency="USD")
        with pytest.raises(ValidationError):
            m.amount = Decimal("999")  # type: ignore[misc]


class TestBoundingBox:
    def test_valid_box(self) -> None:
        box = BoundingBox(left=0.1, top=0.2, right=0.5, bottom=0.4)
        assert box.right > box.left

    def test_inverted_horizontal_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="right"):
            BoundingBox(left=0.9, top=0.1, right=0.2, bottom=0.5)

    def test_inverted_vertical_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="bottom"):
            BoundingBox(left=0.1, top=0.9, right=0.5, bottom=0.2)

    def test_out_of_range_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BoundingBox(left=-0.1, top=0.1, right=0.5, bottom=0.5)


class TestExtractedProvenance:
    def test_defaults_are_trusted_structure(self) -> None:
        e = Extracted[str](value="INV-001")
        assert e.confidence == 1.0
        assert e.method is ExtractionMethod.PARSED_STRUCTURE

    def test_confidence_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Extracted[str](value="x", confidence=1.5)

    def test_alias_rule_and_human_are_trusted(self) -> None:
        assert Extracted[str](value="x", method=ExtractionMethod.ALIAS_RULE).is_trusted
        assert Extracted[str](value="x", method=ExtractionMethod.HUMAN).is_trusted

    def test_vision_and_ocr_are_not_trusted(self) -> None:
        # These are probabilistic reads and must remain gateable by confidence.
        assert not Extracted[str](value="x", method=ExtractionMethod.VISION).is_trusted
        assert not Extracted[str](value="x", method=ExtractionMethod.OCR).is_trusted

    def test_human_correction_preserves_region_and_lifts_confidence(self) -> None:
        region = SourceRegion(page=1, kind=RegionKind.TABLE, element_ref="tbl-1")
        original = Extracted[str](
            value="1O0.00",
            confidence=0.41,
            method=ExtractionMethod.OCR,
            region=region,
            source_label="Grand Total",
        )
        corrected = original.with_human_correction("100.00")

        assert corrected.value == "100.00"
        assert corrected.confidence == 1.0
        assert corrected.method is ExtractionMethod.HUMAN
        assert corrected.region == region
        assert corrected.source_label == "Grand Total"
        # Original is untouched — corrections are new facts, not edits.
        assert original.value == "1O0.00"

    def test_source_label_retained_for_alias_learning(self) -> None:
        e = Extracted[str](value="500.00", source_label="Amount Payable")
        assert e.source_label == "Amount Payable"
