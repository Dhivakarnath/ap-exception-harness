"""Value types shared across the canonical model.

Two non-negotiable decisions live here.

**Money is Decimal, never float.** An AP system authorises payments. Binary
floating point cannot represent `0.10` exactly, so `0.1 + 0.2 != 0.3`, and
accumulated error in a line-item sum would make the `math_integrity` check
(FR-4.7) unreliable — the very check meant to catch arithmetic problems. All
monetary values are `Decimal`, quantised to the currency's minor unit, and
compared exactly.

**Extracted values carry provenance.** Every field the model produces is wrapped
in `Extracted[T]`, which pairs the value with a confidence score and a reference
back to the region of the source document it came from (FR-2.5). This is what
makes the UI's highlight-back possible, and what lets confidence gate
auto-approval instead of being decorative.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Number of decimal places for currencies we handle. ISO 4217 minor units.
# Deliberately a small explicit table: silently assuming 2 places would be wrong
# for JPY (0) and would corrupt rounding for KWD/BHD (3).
_CURRENCY_MINOR_UNITS: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "AUD": 2,
    "CHF": 2,
    "INR": 2,
    "SGD": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "BHD": 3,
}

SUPPORTED_CURRENCIES: frozenset[str] = frozenset(_CURRENCY_MINOR_UNITS)


class CurrencyError(ValueError):
    """Unsupported or inconsistent currency."""


def minor_units(currency: str) -> int:
    """Decimal places for a currency code."""
    try:
        return _CURRENCY_MINOR_UNITS[currency.upper()]
    except KeyError as exc:
        raise CurrencyError(
            f"Unsupported currency {currency!r}. "
            f"Supported: {', '.join(sorted(SUPPORTED_CURRENCIES))}. "
            "Refusing to guess minor units — rounding would be wrong."
        ) from exc


def quantise(amount: Decimal, currency: str) -> Decimal:
    """Round to the currency's minor unit using half-up.

    Half-up (not banker's rounding) matches conventional invoice arithmetic,
    which is what we are reconciling against.
    """
    places = minor_units(currency)
    exponent = Decimal(1).scaleb(-places)
    try:
        return amount.quantize(exponent, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:  # pragma: no cover - guards absurd magnitudes
        raise CurrencyError(f"Cannot quantise {amount} to {places} places") from exc


class Money(BaseModel):
    """An exact monetary amount in a specific currency.

    Arithmetic refuses to mix currencies rather than converting silently — an
    implicit FX conversion inside a reconciliation check would be a correctness
    bug that is very hard to see.
    """

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _normalise_currency(cls, v: str) -> str:
        code = v.upper()
        minor_units(code)  # raises for unsupported codes
        return code

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, v: object) -> object:
        # Accept int/str from JSON and OCR output. Reject float: converting a
        # float to Decimal imports the binary representation error we are
        # trying to avoid.
        if isinstance(v, float):
            # ValueError (not TypeError) so Pydantic wraps this into a
            # ValidationError that names the offending field.
            raise ValueError(
                "Money.amount must not be a float — pass a str, int, or Decimal. "
                "Float would introduce binary rounding error into payment arithmetic."
            )
        if isinstance(v, (int, str)):
            try:
                return Decimal(v)
            except InvalidOperation as exc:
                raise ValueError(f"Not a valid decimal amount: {v!r}") from exc
        return v

    @model_validator(mode="after")
    def _quantise(self) -> Self:
        # Frozen model: mutate via object.__setattr__ during validation.
        object.__setattr__(self, "amount", quantise(self.amount, self.currency))
        return self

    # ---------------------------------------------------------------- helpers
    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(amount=Decimal(0), currency=currency)

    def _assert_same_currency(self, other: Money, op: str) -> None:
        if self.currency != other.currency:
            raise CurrencyError(
                f"Cannot {op} {self.currency} and {other.currency}. "
                "Implicit FX conversion is not permitted in reconciliation."
            )

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other, "add")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other, "subtract")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        if isinstance(factor, float):
            raise TypeError("Refusing to multiply Money by float; use Decimal.")
        return Money(amount=self.amount * Decimal(factor), currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._assert_same_currency(other, "compare")
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._assert_same_currency(other, "compare")
        return self.amount <= other.amount

    def abs_difference(self, other: Money) -> Money:
        self._assert_same_currency(other, "compare")
        return Money(amount=abs(self.amount - other.amount), currency=self.currency)

    def variance_pct_against(self, baseline: Money) -> Decimal:
        """Signed percentage variance of this amount against a baseline.

        Used by tolerance evaluation (FR-4.2). A zero baseline with a non-zero
        actual is an unbounded variance, reported as a large sentinel rather
        than raising, so the tolerance rule can simply fail it.
        """
        self._assert_same_currency(baseline, "compare")
        if baseline.amount == 0:
            return Decimal(0) if self.amount == 0 else Decimal("Infinity")
        return ((self.amount - baseline.amount) / baseline.amount) * Decimal(100)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


# --------------------------------------------------------------------- regions


class RegionKind(StrEnum):
    """Docling layout labels we care about.

    Docling's layout model emits a richer set; these are the ones that carry
    invoice meaning or that we must be able to point a reviewer at.
    """

    TEXT = "text"
    TABLE = "table"
    PICTURE = "picture"
    TITLE = "title"
    SECTION_HEADER = "section_header"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    LIST_ITEM = "list_item"
    CAPTION = "caption"
    FORMULA = "formula"
    FOOTNOTE = "footnote"
    UNKNOWN = "unknown"


class BoundingBox(BaseModel):
    """Normalised (0..1) box so it survives rescaling in the UI."""

    model_config = ConfigDict(frozen=True)

    left: float = Field(ge=0.0, le=1.0)
    top: float = Field(ge=0.0, le=1.0)
    right: float = Field(ge=0.0, le=1.0)
    bottom: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_ordering(self) -> Self:
        if self.right < self.left:
            raise ValueError(f"right ({self.right}) < left ({self.left})")
        if self.bottom < self.top:
            raise ValueError(f"bottom ({self.bottom}) < top ({self.top})")
        return self


class SourceRegion(BaseModel):
    """Where in the source document a value came from.

    Enables the "show your work" overlay (FR-2.5): a reviewer clicks a field and
    sees the exact spot on the invoice it was read from, rather than trusting an
    assertion.
    """

    model_config = ConfigDict(frozen=True)

    page: int = Field(ge=1)
    kind: RegionKind = RegionKind.UNKNOWN
    bbox: BoundingBox | None = None
    # Docling element identifier, when available, for exact round-tripping.
    element_ref: str | None = None
    snippet: str | None = Field(default=None, max_length=500)


# ------------------------------------------------------------------ extraction

# Confidence is a plain 0..1 float: it is a score for gating, never used in
# monetary arithmetic, so float is appropriate here.
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class ExtractionMethod(StrEnum):
    """How a value was obtained. Feeds the audit trail and the UI."""

    PARSED_STRUCTURE = "parsed_structure"
    """Read from Docling's structured output (preferred)."""

    VISION = "vision"
    """Read by the multimodal model from a page or figure image."""

    OCR = "ocr"
    """Read via OCR for a scanned / image-only document."""

    ALIAS_RULE = "alias_rule"
    """Bound by a previously-confirmed deterministic alias rule (FR-3.2)."""

    DERIVED = "derived"
    """Computed deterministically from other extracted values."""

    HUMAN = "human"
    """Supplied or corrected by a reviewer."""


class Extracted[T](BaseModel):
    """A value plus its provenance.

    Deliberately explicit rather than storing bare values: confidence gates
    auto-approval, method distinguishes a trusted alias rule from a model guess,
    and region drives highlight-back.
    """

    model_config = ConfigDict(frozen=True)

    value: T
    confidence: Confidence = 1.0
    method: ExtractionMethod = ExtractionMethod.PARSED_STRUCTURE
    region: SourceRegion | None = None
    # The label as it literally appeared on the document ("Grand Total"),
    # retained so unseen vendor labels can be promoted into alias rules.
    source_label: str | None = None

    @property
    def is_human_supplied(self) -> bool:
        return self.method is ExtractionMethod.HUMAN

    @property
    def is_trusted(self) -> bool:
        """Human input and confirmed alias rules are not probabilistic."""
        return self.method in (ExtractionMethod.HUMAN, ExtractionMethod.ALIAS_RULE)

    def with_human_correction(self, value: T) -> Extracted[T]:
        """Return a corrected copy at full confidence, preserving the region."""
        return Extracted[T](
            value=value,
            confidence=1.0,
            method=ExtractionMethod.HUMAN,
            region=self.region,
            source_label=self.source_label,
        )
