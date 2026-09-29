"""Tenant policy packs — the configuration half of the policy engine.

The engine (how a rule is evaluated) is built once. The policy (what the rule
actually is) is per-tenant declarative configuration. Onboarding a customer is
therefore a policy pack plus a connector, not a fork of the codebase
(FR-4.12, FR-14.1; ADR-004).

Two properties are deliberate:

**Packs are versioned.** A rule change is a new version, so a decision can
always be replayed against the pack that produced it. An audit trail that cannot
reproduce its own reasoning is not an audit trail.

**Loading fails loudly.** A malformed pack raises rather than falling back to
defaults. Silently applying a default tolerance to a customer who specified a
stricter one is exactly the class of error this system exists to prevent.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ap_agent.core.primitives import SUPPORTED_CURRENCIES, Money


class PolicyPackError(ValueError):
    """A pack is missing, malformed, or internally inconsistent."""


# ------------------------------------------------------------------ tolerances


class Tolerances(BaseModel):
    """Variance allowed before a three-way match is treated as an exception.

    Without tolerances a match is a binary gate and the exception queue floods
    with trivial variances — the failure mode real AP teams hit (FR-4.2).
    """

    model_config = ConfigDict(frozen=True)

    price_pct: Decimal = Field(
        default=Decimal("2.0"),
        ge=0,
        le=100,
        description="Allowed unit-price variance, percent.",
    )
    quantity_pct: Decimal = Field(
        default=Decimal("0.0"),
        ge=0,
        le=100,
        description="Allowed quantity variance, percent. Often zero: you either "
        "received the units or you did not.",
    )
    total_absolute: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        description="Allowed absolute variance on the invoice total, in the "
        "tenant's base currency. Absorbs rounding differences.",
    )
    allow_partial_delivery: bool = Field(
        default=True,
        description="Whether an invoice may bill less than the PO quantity when "
        "the GRN confirms a partial receipt.",
    )
    allow_overbilling: bool = Field(
        default=False,
        description="Whether billed quantity may exceed received quantity. "
        "Almost always false — this is how overpayment happens.",
    )


# ------------------------------------------------------------------ thresholds


class Thresholds(BaseModel):
    """Amount boundaries that decide routing."""

    model_config = ConfigDict(frozen=True)

    touchless_max: Decimal = Field(
        default=Decimal("2500.00"),
        ge=0,
        description="Maximum amount eligible for straight-through processing on "
        "a clean match. Set to 0 to review every invoice (FR-11.5).",
    )
    min_confidence_for_touchless: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Extraction confidence required for auto-approval.",
    )
    min_confidence_for_gl_coding: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Confidence required to auto-apply proposed GL coding. "
        "A citation is required regardless (FR-5.4).",
    )
    threshold_avoidance_band_pct: Decimal = Field(
        default=Decimal("5.0"),
        ge=0,
        le=100,
        description="How close below an approval boundary an amount must sit to "
        "be flagged as possible threshold avoidance (FR-4.8).",
    )
    new_vendor_days: int = Field(
        default=30,
        ge=0,
        description="A vendor first seen within this many days of the invoice "
        "is flagged as new. A documented AP intake fraud signal on its own, and "
        "sharper still when paired with threshold avoidance (FR-4.8).",
    )


# ------------------------------------------------------------------ DOA matrix


class DOABand(BaseModel):
    """One row of the delegation-of-authority matrix.

    Real DOA matrices are role x transaction type x amount band. Routing rules
    are written from the DOA, not the org chart (FR-4.10).
    """

    model_config = ConfigDict(frozen=True)

    tier: str = Field(min_length=1, description="Approver tier, e.g. 'controller'.")
    max_amount: Decimal | None = Field(
        default=None,
        ge=0,
        description="Upper bound for this tier. None means unbounded (top tier).",
    )
    roles: tuple[str, ...] = Field(
        default=(),
        description="Roles that may approve at this tier.",
    )


class DOAMatrix(BaseModel):
    """Ordered escalation ladder."""

    model_config = ConfigDict(frozen=True)

    bands: tuple[DOABand, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_ladder(self) -> Self:
        # Bands must ascend, and exactly one unbounded top tier must exist,
        # otherwise a large invoice could fall through with no approver.
        seen_unbounded = False
        previous: Decimal | None = None

        for band in self.bands:
            if seen_unbounded:
                raise ValueError(
                    "DOA bands after an unbounded tier are unreachable. "
                    f"Tier {band.tier!r} follows an unbounded tier."
                )
            if band.max_amount is None:
                seen_unbounded = True
                continue
            if previous is not None and band.max_amount <= previous:
                raise ValueError(
                    f"DOA bands must ascend: tier {band.tier!r} has "
                    f"max_amount {band.max_amount} after {previous}."
                )
            previous = band.max_amount

        if not seen_unbounded:
            raise ValueError(
                "DOA matrix has no unbounded top tier. An invoice above the "
                "highest band would have no valid approver."
            )
        return self

    def tier_for(self, amount: Decimal) -> DOABand:
        """Lowest tier authorised for this amount."""
        for band in self.bands:
            if band.max_amount is None or amount <= band.max_amount:
                return band
        # Unreachable: the validator guarantees an unbounded tier.
        raise PolicyPackError(f"No DOA tier for amount {amount}")

    def boundaries(self) -> tuple[Decimal, ...]:
        """Bounded band edges — the values threshold-avoidance checks against."""
        return tuple(b.max_amount for b in self.bands if b.max_amount is not None)


# -------------------------------------------------------------- SoD and duties


class SoDRules(BaseModel):
    """Segregation of duties.

    Constant control principle across industries: the person who enters or codes
    an invoice must not approve it, and neither should pay it (FR-4.11).
    """

    model_config = ConfigDict(frozen=True)

    agent_may_code: bool = True
    agent_may_route: bool = True
    agent_may_approve_below_touchless: bool = Field(
        default=True,
        description="Whether the deterministic engine may finalise a clean "
        "match under the touchless threshold. The upstream PO approval is the "
        "human authorisation in that case (FR-11.4).",
    )
    agent_may_approve_above_touchless: bool = Field(
        default=False,
        description="Must remain false. Present so the invariant is explicit in "
        "configuration rather than implied by code.",
    )
    approver_must_differ_from_coder: bool = True
    approver_must_differ_from_payer: bool = True

    @model_validator(mode="after")
    def _forbid_unsafe_configuration(self) -> Self:
        if self.agent_may_approve_above_touchless:
            raise ValueError(
                "agent_may_approve_above_touchless must be false. Allowing the "
                "agent to approve above the touchless threshold breaks "
                "segregation of duties (FR-4.11) and is not configurable."
            )
        return self


# ------------------------------------------------------------------ duplicates


class DuplicateRules(BaseModel):
    """Duplicate detection settings.

    Exact matching catches identical resubmissions; fuzzy matching catches the
    near-misses (`INV-001` vs `INV-001A`, same amount, same week) that exact
    rules wave through (FR-4.3).
    """

    model_config = ConfigDict(frozen=True)

    lookback_days: int = Field(default=90, ge=1, le=1095)
    fuzzy_number_similarity: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Invoice-number similarity above which a near-duplicate is flagged.",
    )
    fuzzy_amount_tolerance: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        description="Absolute amount difference still considered the same amount.",
    )
    fuzzy_date_window_days: int = Field(default=7, ge=0, le=90)


# --------------------------------------------------------------- payment terms


class PaymentTermsDefaults(BaseModel):
    """Fallbacks when an invoice does not state terms.

    Early-payment discounts are a genuine ROI lever: 2/10 net 30 is worth
    roughly 36.7% annualised, so discount capture is tracked as a KPI (FR-4.9).
    """

    model_config = ConfigDict(frozen=True)

    default_terms: str = Field(default="NET30", description="e.g. NET30, 2/10 NET30.")
    capture_discounts: bool = True
    min_discount_annualised_pct: Decimal = Field(
        default=Decimal("10.0"),
        ge=0,
        description="Only flag a discount worth capturing above this annualised rate.",
    )


# --------------------------------------------------------------------- the pack


class PolicyPack(BaseModel):
    """A complete, versioned tenant configuration."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1)
    version: str = Field(min_length=1, description="Bumped on every rule change.")
    industry: str = Field(min_length=1)
    description: str | None = None

    base_currency: str = Field(min_length=3, max_length=3)
    tolerances: Tolerances = Tolerances()
    thresholds: Thresholds = Thresholds()
    doa: DOAMatrix
    sod: SoDRules = SoDRules()
    duplicates: DuplicateRules = DuplicateRules()
    payment_terms: PaymentTermsDefaults = PaymentTermsDefaults()

    require_grn: bool = Field(
        default=True,
        description="Whether a goods receipt is mandatory for PO-backed "
        "invoices. False degrades to two-way matching, which loses the "
        "'did we actually receive it' control.",
    )
    allow_non_po_invoices: bool = Field(
        default=True,
        description="Whether invoices without a PO may be processed at all. "
        "Retail typically must; strict manufacturing may not.",
    )

    @model_validator(mode="after")
    def _validate_pack(self) -> Self:
        currency = self.base_currency.upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise ValueError(
                f"base_currency {self.base_currency!r} is not supported. "
                f"Supported: {', '.join(sorted(SUPPORTED_CURRENCIES))}"
            )

        # The touchless ceiling must sit inside the lowest DOA band, otherwise
        # the engine would auto-approve amounts the DOA reserves for a human.
        bounded = self.doa.boundaries()
        if bounded and self.thresholds.touchless_max > min(bounded):
            raise ValueError(
                f"touchless_max ({self.thresholds.touchless_max}) exceeds the "
                f"lowest DOA band ({min(bounded)}). This would auto-approve "
                "amounts the delegation of authority reserves for a human."
            )
        return self

    # ---------------------------------------------------------------- helpers
    @property
    def touchless_ceiling(self) -> Money:
        return Money(amount=self.thresholds.touchless_max, currency=self.base_currency)

    def is_within_touchless(self, amount: Money) -> bool:
        if amount.currency != self.base_currency:
            # Cross-currency invoices are never touchless: the tenant's
            # thresholds are denominated in one currency and we do not convert.
            return False
        return amount <= self.touchless_ceiling

    @property
    def identity(self) -> str:
        return f"{self.tenant_id}@{self.version}"


# ---------------------------------------------------------------------- loader


def load_policy_pack(path: Path) -> PolicyPack:
    """Load and validate one pack. Raises `PolicyPackError` on any problem."""
    if not path.is_file():
        raise PolicyPackError(f"Policy pack not found: {path}")

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyPackError(f"Policy pack {path.name} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise PolicyPackError(
            f"Policy pack {path.name} must be a YAML mapping, got {type(raw).__name__}"
        )

    try:
        return PolicyPack.model_validate(raw)
    except Exception as exc:
        raise PolicyPackError(
            f"Policy pack {path.name} failed validation. Refusing to fall back "
            f"to defaults — a wrong tolerance is a wrong payment.\n{exc}"
        ) from exc


def load_all_policy_packs(directory: Path) -> dict[str, PolicyPack]:
    """Load every `*.yaml` pack in a directory, keyed by tenant id."""
    if not directory.is_dir():
        raise PolicyPackError(f"Policy pack directory not found: {directory}")

    packs: dict[str, PolicyPack] = {}
    for path in sorted(directory.glob("*.yaml")):
        pack = load_policy_pack(path)
        if pack.tenant_id in packs:
            raise PolicyPackError(
                f"Duplicate tenant_id {pack.tenant_id!r} in {path.name}: "
                "each tenant must have exactly one active pack."
            )
        packs[pack.tenant_id] = pack

    if not packs:
        raise PolicyPackError(f"No policy packs found in {directory}")
    return packs
