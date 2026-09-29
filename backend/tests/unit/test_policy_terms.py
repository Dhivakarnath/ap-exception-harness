"""Payment terms parsing and the `payment_terms` check (FR-4.9)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ap_agent.core.checks import Verdict
from ap_agent.core.policy_pack import PaymentTermsDefaults
from ap_agent.policy.terms import PAYMENT_TERMS, parse_terms, payment_terms
from tests.unit.policy_fixtures import invoice

_DEFAULTS = PaymentTermsDefaults(default_terms="NET30")


class TestParseTerms:
    def test_plain_net_terms(self) -> None:
        parsed = parse_terms("Net 30", _DEFAULTS)
        assert parsed.net_days == 30
        assert parsed.has_discount is False
        assert parsed.recognised is True

    def test_net_terms_without_space(self) -> None:
        parsed = parse_terms("NET45", _DEFAULTS)
        assert parsed.net_days == 45

    def test_discount_terms(self) -> None:
        parsed = parse_terms("2/10 NET30", _DEFAULTS)
        assert parsed.net_days == 30
        assert parsed.discount_pct == Decimal("2")
        assert parsed.discount_days == 10
        assert parsed.has_discount is True

    def test_discount_terms_with_comma(self) -> None:
        parsed = parse_terms("2/10, net 30", _DEFAULTS)
        assert parsed.discount_pct == Decimal("2")
        assert parsed.net_days == 30

    def test_due_on_receipt(self) -> None:
        parsed = parse_terms("Due on receipt", _DEFAULTS)
        assert parsed.net_days == 0
        assert parsed.has_discount is False

    def test_cod(self) -> None:
        parsed = parse_terms("COD", _DEFAULTS)
        assert parsed.net_days == 0

    def test_blank_terms_falls_back_to_default(self) -> None:
        parsed = parse_terms(None, _DEFAULTS)
        assert parsed.net_days == 30
        assert parsed.recognised is True  # the default itself is well-formed

    def test_unrecognised_terms_falls_back_and_is_flagged(self) -> None:
        parsed = parse_terms("Whenever you feel like it", _DEFAULTS)
        assert parsed.net_days == 30
        assert parsed.recognised is False

    def test_due_date_computation(self) -> None:
        parsed = parse_terms("Net 30", _DEFAULTS)
        assert parsed.due_date(date(2026, 2, 27)) == date(2026, 3, 29)

    def test_discount_deadline_computation(self) -> None:
        parsed = parse_terms("2/10 NET30", _DEFAULTS)
        assert parsed.discount_deadline(date(2026, 2, 27)) == date(2026, 3, 9)

    def test_2_10_net_30_annualises_to_roughly_36_percent(self) -> None:
        # The commonly cited figure for this exact term, per
        # `PaymentTermsDefaults`'s own docstring.
        parsed = parse_terms("2/10 NET30", _DEFAULTS)
        annualised = parsed.annualised_discount_pct()
        assert annualised is not None
        assert Decimal("36.0") < annualised < Decimal("37.0")

    def test_no_discount_annualised_is_none(self) -> None:
        parsed = parse_terms("Net 30", _DEFAULTS)
        assert parsed.annualised_discount_pct() is None

    def test_discount_period_zero_or_negative_yields_no_annualised_rate(self) -> None:
        # A discount window equal to or exceeding the net period is
        # degenerate; there is no meaningful "avoided loan period".
        parsed = parse_terms("2/30 NET30", _DEFAULTS)
        assert parsed.annualised_discount_pct() is None


class TestPaymentTermsCheck:
    def test_plain_terms_pass_with_due_date_reported(self) -> None:
        inv = invoice(payment_terms="Net 30", invoice_date_=date(2026, 2, 27))
        result = payment_terms(inv, _DEFAULTS)
        assert result.verdict is Verdict.PASS
        assert result.name == PAYMENT_TERMS
        assert result.inputs["due_date"] == "2026-03-29"

    def test_discount_terms_report_annualised_value(self) -> None:
        inv = invoice(payment_terms="2/10 NET30", invoice_date_=date(2026, 2, 27))
        result = payment_terms(inv, _DEFAULTS)
        assert result.verdict is Verdict.PASS
        assert result.inputs["discount_annualised_pct"] is not None
        assert "Worth capturing" in result.reasoning

    def test_low_value_discount_not_flagged_as_worth_capturing(self) -> None:
        defaults = PaymentTermsDefaults(
            default_terms="NET30", min_discount_annualised_pct=Decimal("50.0")
        )
        inv = invoice(payment_terms="2/10 NET30", invoice_date_=date(2026, 2, 27))
        result = payment_terms(inv, defaults)
        assert "Worth capturing" not in result.reasoning

    def test_unrecognised_terms_flags_and_forces_review(self) -> None:
        inv = invoice(payment_terms="Whenever", invoice_date_=date(2026, 2, 27))
        result = payment_terms(inv, _DEFAULTS)
        assert result.verdict is Verdict.FLAG
        assert result.forces_review is True
        assert result.inputs["recognised"] is False

    def test_no_printed_terms_uses_default(self) -> None:
        inv = invoice(payment_terms=None, invoice_date_=date(2026, 2, 27))
        result = payment_terms(inv, _DEFAULTS)
        assert result.inputs["due_date"] == "2026-03-29"

    def test_capture_discounts_disabled_never_reports_worth_capturing(self) -> None:
        defaults = PaymentTermsDefaults(default_terms="NET30", capture_discounts=False)
        inv = invoice(payment_terms="2/10 NET30", invoice_date_=date(2026, 2, 27))
        result = payment_terms(inv, defaults)
        assert "Worth capturing" not in result.reasoning
