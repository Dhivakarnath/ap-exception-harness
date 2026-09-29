"""`doa_route` and `sod_check` (FR-4.10, FR-4.11)."""

from __future__ import annotations

import pytest

from ap_agent.core.checks import Verdict
from ap_agent.core.policy_pack import SoDRules
from ap_agent.errors import PolicyPackInvariantError
from ap_agent.policy.authority import DOA_ROUTE, SOD_CHECK, doa_route, sod_check
from tests.unit.policy_fixtures import context, doa_matrix, invoice, policy_pack


class TestDoaRoute:
    def test_touchless_amount_reports_touchless_true(self) -> None:
        inv = invoice(total_amount="500.00", subtotal="500.00", tax_amount=None)
        result = doa_route(context(inv=inv))
        assert result.verdict is Verdict.PASS
        assert result.name == DOA_ROUTE
        assert result.inputs["touchless"] is True

    def test_amount_above_touchless_reports_touchless_false(self) -> None:
        inv = invoice(total_amount="4000.00", subtotal="4000.00", tax_amount=None)
        result = doa_route(context(inv=inv))
        assert result.inputs["touchless"] is False
        assert "human approver is required" in result.reasoning

    def test_reports_the_correct_tier(self) -> None:
        pack = policy_pack(
            doa=doa_matrix(("buyer", "5000.00"), ("controller", "25000.00"), ("cfo", None))
        )
        inv = invoice(total_amount="10000.00", subtotal="10000.00", tax_amount=None)
        result = doa_route(context(inv=inv, policy=pack))
        assert result.inputs["tier"] == "controller"

    def test_unbounded_top_tier_for_very_large_amount(self) -> None:
        pack = policy_pack(
            doa=doa_matrix(("buyer", "5000.00"), ("controller", "25000.00"), ("cfo", None))
        )
        inv = invoice(total_amount="999999.00", subtotal="999999.00", tax_amount=None)
        result = doa_route(context(inv=inv, policy=pack))
        assert result.inputs["tier"] == "cfo"

    def test_foreign_currency_flags_rather_than_comparing(self) -> None:
        inv = invoice(currency="EUR", total_amount="500.00", subtotal="500.00", tax_amount=None)
        result = doa_route(context(inv=inv))
        assert result.verdict is Verdict.FLAG
        assert result.forces_review is True


class TestSodCheck:
    def test_no_identities_supplied_skips(self) -> None:
        result = sod_check(context())
        assert result.verdict is Verdict.SKIP
        assert result.name == SOD_CHECK

    def test_distinct_identities_pass(self) -> None:
        result = sod_check(
            context(
                coder_identity="alice@example.com",
                approver_identity="bob@example.com",
                payer_identity="carol@example.com",
            )
        )
        assert result.verdict is Verdict.PASS

    def test_coder_and_approver_collision_fails(self) -> None:
        result = sod_check(
            context(coder_identity="alice@example.com", approver_identity="alice@example.com")
        )
        assert result.verdict is Verdict.FAIL
        assert result.severity.value == "critical"
        assert result.forces_review is True
        assert "coder and approver" in result.reasoning

    def test_approver_and_payer_collision_fails(self) -> None:
        result = sod_check(
            context(approver_identity="bob@example.com", payer_identity="bob@example.com")
        )
        assert result.verdict is Verdict.FAIL
        assert "approver and payer" in result.reasoning

    def test_both_collisions_are_both_reported(self) -> None:
        result = sod_check(
            context(
                coder_identity="alice@example.com",
                approver_identity="alice@example.com",
                payer_identity="alice@example.com",
            )
        )
        assert result.verdict is Verdict.FAIL
        assert "coder and approver" in result.reasoning
        assert "approver and payer" in result.reasoning

    def test_only_coder_and_approver_supplied_partial_identity_check_runs(self) -> None:
        # approver_must_differ_from_payer cannot be evaluated without a payer
        # identity, but the coder/approver comparison must still run.
        result = sod_check(
            context(coder_identity="alice@example.com", approver_identity="bob@example.com")
        )
        assert result.verdict is Verdict.PASS

    def test_rule_disabled_does_not_flag_a_collision(self) -> None:
        pack = policy_pack(sod=SoDRules(approver_must_differ_from_coder=False))
        result = sod_check(
            context(
                policy=pack,
                coder_identity="alice@example.com",
                approver_identity="alice@example.com",
                payer_identity="carol@example.com",
            )
        )
        assert result.verdict is Verdict.PASS

    def test_invariant_violation_on_a_pack_that_bypassed_schema_validation(self) -> None:
        pack = policy_pack()
        # `model_construct` bypasses validation entirely, simulating a pack
        # that reached the engine without going through `PolicyPack.
        # model_validate` — the only way `agent_may_approve_above_touchless`
        # could ever be True here, since the schema forbids it otherwise.
        unsafe_sod = SoDRules.model_construct(
            agent_may_code=True,
            agent_may_route=True,
            agent_may_approve_below_touchless=True,
            agent_may_approve_above_touchless=True,
            approver_must_differ_from_coder=True,
            approver_must_differ_from_payer=True,
        )
        unsafe_pack = pack.model_copy(update={"sod": unsafe_sod})
        with pytest.raises(PolicyPackInvariantError, match="agent_may_approve_above_touchless"):
            sod_check(context(policy=unsafe_pack))
