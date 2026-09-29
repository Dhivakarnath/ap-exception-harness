"""Check results — the shared vocabulary of the whole system.

Every deterministic rule (FR-4.x), guardrail, and retrieval gate produces a
`CheckResult`. The same object is:

  * persisted to `check_results` as part of the audit trail (FR-10.2),
  * emitted as a `custom` stream event so the UI can render it live (FR-12.2),
  * attached to an OTEL span as attributes (FR-12.1),
  * asserted against in policy-adherence evals (FR-13.4).

Because one structure serves all four, a rule cannot be "checked" without
simultaneously being observable, auditable, and testable. That is the point.

A result always records **threshold vs actual**, not just a verdict. "Failed" is
not useful to a reviewer; "price variance 4.2% exceeded the 2.0% tolerance" is.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Verdict(StrEnum):
    """Outcome of a single check.

    Four states, not two. A binary pass/fail would collapse the distinction
    between "this is fine", "a human should look at this", and "this is
    definitively wrong" — which is exactly the distinction that decides routing.
    """

    PASS = "pass"  # noqa: S105 - a verdict, not a credential
    """Satisfied. Does not block straight-through processing."""

    FLAG = "flag"
    """Suspicious but not disqualifying. Raises attention and may force review."""

    FAIL = "fail"
    """Violated. Blocks auto-approval."""

    SKIP = "skip"
    """Not applicable (e.g. three-way match on a non-PO invoice). Recorded
    explicitly rather than omitted, so a reviewer can tell the difference
    between "passed" and "never ran"."""


class Severity(StrEnum):
    """How much a non-PASS verdict matters."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    """Always escalates regardless of amount or thresholds — e.g. a vendor bank
    detail change (FR-4.6)."""


class CheckCategory(StrEnum):
    """Grouping for the UI ledger and for per-category eval reporting."""

    DOCUMENT = "document"
    """Parse / extraction integrity."""

    ARITHMETIC = "arithmetic"
    """Math integrity, currency consistency."""

    IDENTITY = "identity"
    """Vendor resolution, bank details, field mapping."""

    DUPLICATE = "duplicate"
    MATCHING = "matching"
    """Three-way match and tolerance evaluation."""

    FRAUD = "fraud"
    """Threshold avoidance and related red flags."""

    AUTHORITY = "authority"
    """DOA routing and segregation of duties."""

    CODING = "coding"
    """GL coding and its grounding."""

    TERMS = "terms"
    """Payment terms and discount computation."""

    GUARDRAIL = "guardrail"
    """PII, tool permissions, argument validation."""


class CheckResult(BaseModel):
    """Outcome of one rule evaluation.

    Immutable: a check result is a historical fact. Corrections are new results,
    not edits, which keeps the audit trail honest.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, description="Stable rule identifier, e.g. 'three_way_match'.")
    category: CheckCategory
    verdict: Verdict
    severity: Severity = Severity.INFO

    reasoning: str = Field(
        min_length=1,
        description="Human-readable explanation. Rendered verbatim in the UI, "
        "so it must stand alone without the code beside it.",
    )

    # Threshold vs actual is what makes a verdict actionable. Kept as strings so
    # any comparable type (Money, Decimal, count, date) renders faithfully
    # without a lossy numeric coercion.
    threshold: str | None = None
    actual: str | None = None

    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="The values the rule actually saw. Reproduces the decision.",
    )

    # Set when a check on its own forces human review, independent of amount
    # thresholds (FR-4.6, FR-5.4, FR-4.8).
    forces_review: bool = False

    duration_ms: float | None = Field(default=None, ge=0)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Populated for checks whose conclusion must be grounded in retrieved
    # evidence, e.g. GL coding (FR-5.3).
    citations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_coherence(self) -> Self:
        if self.verdict is Verdict.PASS and self.forces_review:
            raise ValueError(
                f"Check {self.name!r} passed but sets forces_review. "
                "Use FLAG for 'acceptable but needs eyes' so the ledger stays honest."
            )
        if self.severity is Severity.CRITICAL and self.verdict is Verdict.PASS:
            raise ValueError(
                f"Check {self.name!r} is CRITICAL severity with a PASS verdict. "
                "Severity describes a problem; a passing check has none."
            )
        return self

    # ---------------------------------------------------------------- helpers
    @property
    def blocks_auto_approval(self) -> bool:
        """Whether this result alone prevents touchless processing."""
        return self.verdict is Verdict.FAIL or self.forces_review

    def as_stream_payload(self) -> dict[str, Any]:
        """Shape emitted on the `check` stream channel (design §12.3)."""
        return {
            "name": self.name,
            "category": self.category.value,
            "verdict": self.verdict.value,
            "severity": self.severity.value,
            "reasoning": self.reasoning,
            "threshold": self.threshold,
            "actual": self.actual,
            "inputs": self.inputs,
            "forces_review": self.forces_review,
            "duration_ms": self.duration_ms,
            "citations": list(self.citations),
            "at": self.evaluated_at.isoformat(),
        }

    def as_span_attributes(self) -> dict[str, str | float | bool]:
        """Flat attributes for an OTEL span (FR-12.1)."""
        attrs: dict[str, str | float | bool] = {
            "check.name": self.name,
            "check.category": self.category.value,
            "check.verdict": self.verdict.value,
            "check.severity": self.severity.value,
            "check.forces_review": self.forces_review,
        }
        if self.threshold is not None:
            attrs["check.threshold"] = self.threshold
        if self.actual is not None:
            attrs["check.actual"] = self.actual
        if self.duration_ms is not None:
            attrs["check.duration_ms"] = self.duration_ms
        return attrs

    def render_line(self, width: int = 26) -> str:
        """One-line ledger rendering, mirroring the UI (design §11)."""
        glyph = {
            Verdict.PASS: "\u2713",
            Verdict.FLAG: "\u26a0",
            Verdict.FAIL: "\u2717",
            Verdict.SKIP: "\u2014",
        }[self.verdict]
        label = self.name.ljust(width, ".")
        return f"{glyph} {label} {self.verdict.value.upper():5} {self.reasoning}"


class CheckLedger(BaseModel):
    """Ordered results for one invoice run.

    Ordering is evaluation order, which is also the order the UI renders and the
    order a reviewer reasons about.
    """

    model_config = ConfigDict(frozen=True)

    results: tuple[CheckResult, ...] = ()

    def append(self, result: CheckResult) -> CheckLedger:
        return CheckLedger(results=(*self.results, result))

    def extend(self, results: list[CheckResult]) -> CheckLedger:
        return CheckLedger(results=(*self.results, *results))

    # ---------------------------------------------------------------- queries
    def by_name(self, name: str) -> CheckResult | None:
        return next((r for r in self.results if r.name == name), None)

    def with_verdict(self, verdict: Verdict) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.verdict is verdict)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return self.with_verdict(Verdict.FAIL)

    @property
    def flags(self) -> tuple[CheckResult, ...]:
        return self.with_verdict(Verdict.FLAG)

    @property
    def critical(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.severity is Severity.CRITICAL)

    @property
    def review_forcing(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.forces_review)

    @property
    def is_clean(self) -> bool:
        """True when nothing blocks straight-through processing.

        FLAG does not by itself block: a flag raises attention, and it is the
        routing policy — not the flag — that decides whether that means review.
        A flag that must block sets `forces_review`.
        """
        return not any(r.blocks_auto_approval for r in self.results)

    def render(self) -> str:
        """Full ledger as text. Used in CLI demos and eval reports."""
        return "\n".join(r.render_line() for r in self.results)

    def __len__(self) -> int:
        return len(self.results)

    def iter_results(self) -> Iterator[CheckResult]:
        """Explicit iterator.

        Named rather than overriding `__iter__`: Pydantic's BaseModel uses
        `__iter__` for field iteration, and shadowing it would break
        `dict(model)` and serialisation in surprising ways.
        """
        return iter(self.results)
