"""`threshold_avoidance` (FR-4.8).

Mirrors `build_threshold_avoidance` in `apfixtures.cases`: an amount parked
just under the manufacturing pack's 5000.00 `buyer` DOA boundary.
"""

from __future__ import annotations

from decimal import Decimal

from ap_agent.core.checks import Verdict
from ap_agent.policy.fraud import THRESHOLD_AVOIDANCE, threshold_avoidance
from tests.unit.policy_fixtures import context, doa_matrix, invoice, policy_pack


def _pack_with_5000_boundary() -> object:
    return policy_pack(
        doa=doa_matrix(("buyer", "5000.00"), ("controller", "25000.00"), ("cfo", None)),
        thresholds=policy_pack().thresholds.model_copy(
            update={"threshold_avoidance_band_pct": Decimal("5.0")}
        ),
    )


class TestThresholdAvoidance:
    def test_amount_well_clear_of_any_boundary_passes(self) -> None:
        pack = _pack_with_5000_boundary()
        inv = invoice(total_amount="500.00", subtotal="500.00", tax_amount=None)
        result = threshold_avoidance(context(inv=inv, policy=pack))
        assert result.verdict is Verdict.PASS
        assert result.name == THRESHOLD_AVOIDANCE

    def test_amount_just_below_boundary_flags(self) -> None:
        pack = _pack_with_5000_boundary()
        # 4900 is 2% below 5000, inside the 5% watch band.
        inv = invoice(total_amount="4900.00", subtotal="4900.00", tax_amount=None)
        result = threshold_avoidance(context(inv=inv, policy=pack))
        assert result.verdict is Verdict.FLAG
        assert result.forces_review is True
        assert "5000" in result.reasoning

    def test_amount_far_below_boundary_but_outside_band_passes(self) -> None:
        pack = _pack_with_5000_boundary()
        # 4000 is 20% below 5000, well outside the 5% band.
        inv = invoice(total_amount="4000.00", subtotal="4000.00", tax_amount=None)
        result = threshold_avoidance(context(inv=inv, policy=pack))
        assert result.verdict is Verdict.PASS

    def test_amount_exactly_at_boundary_also_flags(self) -> None:
        # Landing exactly on the boundary is at least as suspicious as
        # landing just under it (0% gap is inside any positive watch band),
        # not a reason to relax scrutiny.
        pack = _pack_with_5000_boundary()
        inv = invoice(total_amount="5000.00", subtotal="5000.00", tax_amount=None)
        result = threshold_avoidance(context(inv=inv, policy=pack))
        assert result.verdict is Verdict.FLAG

    def test_amount_above_the_lowest_boundary_checks_the_next_one(self) -> None:
        pack = _pack_with_5000_boundary()
        # 24000 is 4% below the 25000 controller boundary.
        inv = invoice(total_amount="24000.00", subtotal="24000.00", tax_amount=None)
        result = threshold_avoidance(context(inv=inv, policy=pack))
        assert result.verdict is Verdict.FLAG
        assert "25000" in result.reasoning

    def test_foreign_currency_skips_rather_than_comparing(self) -> None:
        pack = _pack_with_5000_boundary()
        inv = invoice(
            currency="EUR", total_amount="4900.00", subtotal="4900.00", tax_amount=None
        )
        result = threshold_avoidance(context(inv=inv, policy=pack))
        assert result.verdict is Verdict.SKIP
