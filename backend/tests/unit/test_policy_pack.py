"""Policy pack schema, invariants, and loading.

The most important assertions here are the ones that refuse an unsafe
configuration. A pack that silently permits the agent to approve above the
touchless threshold, or that auto-approves amounts the DOA reserves for a human,
would defeat the controls the rest of the system implements.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from ap_agent.core.policy_pack import (
    DOABand,
    DOAMatrix,
    PolicyPack,
    PolicyPackError,
    SoDRules,
    Thresholds,
    Tolerances,
    load_all_policy_packs,
    load_policy_pack,
)
from ap_agent.core.primitives import Money

PACK_DIR = Path(__file__).resolve().parents[2] / "policy_packs"


def _matrix(*bands: tuple[str, str | None]) -> DOAMatrix:
    return DOAMatrix(
        bands=tuple(
            DOABand(tier=t, max_amount=Decimal(a) if a is not None else None) for t, a in bands
        )
    )


def _pack(**overrides: object) -> PolicyPack:
    base: dict[str, object] = {
        "tenant_id": "t1",
        "version": "1.0.0",
        "industry": "test",
        "base_currency": "USD",
        "doa": _matrix(("clerk", "5000.00"), ("cfo", None)),
    }
    return PolicyPack.model_validate({**base, **overrides})


class TestDOALadder:
    def test_valid_ascending_ladder(self) -> None:
        m = _matrix(("a", "1000"), ("b", "5000"), ("c", None))
        assert len(m.bands) == 3

    def test_ladder_must_ascend(self) -> None:
        with pytest.raises(ValueError, match="must ascend"):
            _matrix(("a", "5000"), ("b", "1000"), ("c", None))

    def test_unbounded_top_tier_is_required(self) -> None:
        # Otherwise a very large invoice has no valid approver.
        with pytest.raises(ValueError, match="no unbounded top tier"):
            _matrix(("a", "1000"), ("b", "5000"))

    def test_bands_after_unbounded_are_unreachable(self) -> None:
        with pytest.raises(ValueError, match="unreachable"):
            _matrix(("a", None), ("b", "5000"))

    def test_tier_selection_picks_lowest_authorised(self) -> None:
        m = _matrix(("clerk", "1000"), ("manager", "10000"), ("cfo", None))
        assert m.tier_for(Decimal("500")).tier == "clerk"
        assert m.tier_for(Decimal("1000")).tier == "clerk"  # inclusive bound
        assert m.tier_for(Decimal("1000.01")).tier == "manager"
        assert m.tier_for(Decimal("999999")).tier == "cfo"

    def test_boundaries_exclude_the_unbounded_tier(self) -> None:
        m = _matrix(("clerk", "1000"), ("manager", "10000"), ("cfo", None))
        assert m.boundaries() == (Decimal("1000"), Decimal("10000"))


class TestSoDInvariant:
    def test_agent_approval_above_touchless_is_refused(self) -> None:
        # Not a configurable option — allowing it breaks FR-4.11.
        with pytest.raises(ValueError, match="must be false"):
            SoDRules(agent_may_approve_above_touchless=True)

    def test_defaults_are_safe(self) -> None:
        s = SoDRules()
        assert s.agent_may_approve_above_touchless is False
        assert s.approver_must_differ_from_coder is True
        assert s.approver_must_differ_from_payer is True


class TestPackCoherence:
    def test_touchless_cannot_exceed_lowest_doa_band(self) -> None:
        # Would auto-approve amounts the DOA reserves for a human.
        with pytest.raises(ValueError, match="exceeds the lowest DOA band"):
            _pack(
                doa=_matrix(("clerk", "1000.00"), ("cfo", None)),
                thresholds=Thresholds(touchless_max=Decimal("2000.00")),
            )

    def test_touchless_equal_to_lowest_band_is_allowed(self) -> None:
        p = _pack(
            doa=_matrix(("clerk", "1000.00"), ("cfo", None)),
            thresholds=Thresholds(touchless_max=Decimal("1000.00")),
        )
        assert p.thresholds.touchless_max == Decimal("1000.00")

    def test_unsupported_base_currency_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not supported"):
            _pack(base_currency="ZZZ")

    def test_zero_touchless_means_review_everything(self) -> None:
        # FR-11.5: a conservative tenant reviews every invoice.
        p = _pack(thresholds=Thresholds(touchless_max=Decimal("0")))
        assert p.is_within_touchless(Money(amount="0.01", currency="USD")) is False


class TestTouchlessEvaluation:
    def test_amount_within_ceiling(self) -> None:
        p = _pack(thresholds=Thresholds(touchless_max=Decimal("2500.00")))
        assert p.is_within_touchless(Money(amount="2500.00", currency="USD")) is True
        assert p.is_within_touchless(Money(amount="2500.01", currency="USD")) is False

    def test_foreign_currency_is_never_touchless(self) -> None:
        # Thresholds are denominated in one currency and we never convert.
        p = _pack(thresholds=Thresholds(touchless_max=Decimal("2500.00")))
        assert p.is_within_touchless(Money(amount="10.00", currency="EUR")) is False

    def test_identity_includes_version(self) -> None:
        # Decisions must be replayable against the pack that produced them.
        assert _pack(tenant_id="acme", version="2.1.0").identity == "acme@2.1.0"


class TestToleranceDefaults:
    def test_overbilling_is_disallowed_by_default(self) -> None:
        assert Tolerances().allow_overbilling is False

    def test_quantity_tolerance_defaults_to_zero(self) -> None:
        assert Tolerances().quantity_pct == Decimal("0.0")

    def test_negative_tolerance_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Tolerances(price_pct=Decimal("-1"))


class TestShippedPacks:
    def test_both_starter_packs_load(self) -> None:
        packs = load_all_policy_packs(PACK_DIR)
        assert set(packs) == {"manufacturing-demo", "retail-demo"}

    def test_manufacturing_pack_is_strict(self) -> None:
        pack = load_policy_pack(PACK_DIR / "manufacturing.yaml")
        assert pack.require_grn is True
        assert pack.allow_non_po_invoices is False
        assert pack.tolerances.quantity_pct == Decimal("0.0")
        assert pack.tolerances.allow_overbilling is False

    def test_retail_pack_permits_non_po(self) -> None:
        pack = load_policy_pack(PACK_DIR / "retail_non_po.yaml")
        assert pack.allow_non_po_invoices is True
        assert pack.require_grn is False

    def test_packs_differ_meaningfully(self) -> None:
        # The point of two packs: the same engine, materially different posture.
        mfg = load_policy_pack(PACK_DIR / "manufacturing.yaml")
        retail = load_policy_pack(PACK_DIR / "retail_non_po.yaml")

        assert retail.tolerances.price_pct > mfg.tolerances.price_pct
        assert retail.thresholds.touchless_max < mfg.thresholds.touchless_max
        assert retail.duplicates.lookback_days > mfg.duplicates.lookback_days
        assert retail.require_grn is not mfg.require_grn

    def test_every_pack_satisfies_the_sod_invariant(self) -> None:
        for pack in load_all_policy_packs(PACK_DIR).values():
            assert pack.sod.agent_may_approve_above_touchless is False

    def test_every_pack_has_an_unbounded_top_tier(self) -> None:
        for pack in load_all_policy_packs(PACK_DIR).values():
            assert pack.doa.bands[-1].max_amount is None


class TestLoaderFailsLoudly:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(PolicyPackError, match="not found"):
            load_policy_pack(tmp_path / "nope.yaml")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("tenant_id: [unclosed", encoding="utf-8")
        with pytest.raises(PolicyPackError, match="not valid YAML"):
            load_policy_pack(bad)

    def test_non_mapping_yaml(self, tmp_path: Path) -> None:
        bad = tmp_path / "list.yaml"
        bad.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(PolicyPackError, match="must be a YAML mapping"):
            load_policy_pack(bad)

    def test_schema_violation_does_not_fall_back_to_defaults(self, tmp_path: Path) -> None:
        # The critical property: a wrong tolerance is a wrong payment, so a
        # malformed pack must never silently become a default pack.
        bad = tmp_path / "incoherent.yaml"
        bad.write_text(
            "tenant_id: x\n"
            "version: '1'\n"
            "industry: test\n"
            "base_currency: USD\n"
            "thresholds:\n"
            "  touchless_max: '99999.00'\n"
            "doa:\n"
            "  bands:\n"
            "    - tier: clerk\n"
            "      max_amount: '100.00'\n"
            "    - tier: cfo\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyPackError, match="Refusing to fall back"):
            load_policy_pack(bad)

    def test_empty_directory(self, tmp_path: Path) -> None:
        with pytest.raises(PolicyPackError, match="No policy packs found"):
            load_all_policy_packs(tmp_path)

    def test_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(PolicyPackError, match="directory not found"):
            load_all_policy_packs(tmp_path / "absent")

    def test_duplicate_tenant_id_is_rejected(self, tmp_path: Path) -> None:
        body = (
            "version: '1'\n"
            "industry: test\n"
            "base_currency: USD\n"
            "doa:\n"
            "  bands:\n"
            "    - tier: cfo\n"
        )
        (tmp_path / "a.yaml").write_text(f"tenant_id: same\n{body}", encoding="utf-8")
        (tmp_path / "b.yaml").write_text(f"tenant_id: same\n{body}", encoding="utf-8")
        with pytest.raises(PolicyPackError, match="Duplicate tenant_id"):
            load_all_policy_packs(tmp_path)
