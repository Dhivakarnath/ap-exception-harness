"""Objective extraction confidence.

Nova Lite's self-reported `confidence` is not a usable signal on its own.
Measured on this project's own fixtures across clean, OCR-degraded, and
image-only invoices: the model reported `1.00` on every field of every
document, including a run where it had just admitted (in `unreadable_regions`)
that it could not read the tax line. A confidence score that never moves
cannot gate anything — an approval threshold set below 1.00 would let
everything through, and a threshold at 1.00 would hold everything for review.
Either way, model self-report is decorative rather than a control.

So this module derives confidence from things that can actually be measured
about *how* an extraction was produced, and combines the model's own number
with them rather than trusting it alone:

* **Parse strategy.** OCR is lossier than a digital text layer by construction
  — that is the entire reason the two-pass strategy exists. A reading built on
  an OCR reconstruction starts from a lower ceiling than one built on exact
  characters, regardless of how sure the model sounds about it.
* **Repair attempts.** A field the model got right in one call is a different
  quality of reading than one that only validated after being told what was
  wrong and asked again. The second is real evidence the extraction was
  fragile, not a rounding error to discard once the retry succeeds.
* **Arithmetic reconciliation.** Whether subtotal + tax = total, and whether
  the line-item sum matches the printed subtotal, is checked independently by
  `math_integrity` downstream — but *here* it also functions as a confidence
  signal: a self-consistent document is corroborating evidence that the
  numbers were read correctly, and an inconsistent one is a reason for
  caution even before the policy engine runs.
* **Label attribution.** A value whose printed label was recovered
  structurally (see `mapping.labels`) was read from a location on the page a
  human can point to. A value with no attributable label rests more heavily on
  the model having identified the right thing by convention or inference alone.

None of these signals ever *raises* the model's own number — corroboration
lowers risk, it does not manufacture certainty the model did not report. Every
signal can only pull confidence down from where the model + parse quality
would otherwise place it. That asymmetry matches the cost asymmetry in AP: an
under-confident reading costs a human a few minutes of review, an
over-confident one risks an authorised payment on a wrong number.

The output is not a replacement for `Invoice.header_confidence` — it augments
it. `header_confidence` is `min()` over the fields already on the invoice,
which is correct as a floor; this module explains *why* that floor might be
lower than the model thought, and produces the number that should actually
gate auto-approval.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from ap_agent.core.canonical import Invoice
from ap_agent.extract.extractor import ExtractionOutcome
from ap_agent.ingest.parsing import ParseStrategy

# --- adjustment magnitudes -----------------------------------------------------
#
# Each is a multiplicative penalty in (0, 1], applied to the model-reported
# floor. Multiplicative rather than subtractive so an already-low model
# confidence is not pushed negative, and so several small penalties compound
# rather than each independently zeroing the score.

OCR_PENALTY: Final[float] = 0.90
"""Applied when the parse strategy was OCR rather than a digital text layer.

Measured, not guessed: the fixture set's OCR pass introduces real character
noise that the structural pass does not have, per the two-pass design in
`ingest.parsing`.
"""

ESCALATED_OCR_PENALTY: Final[float] = 0.95
"""Additional penalty (stacked with `OCR_PENALTY`) when the structural pass was
attempted first and rejected as implausible.

A document that could not even produce a plausible text layer is a worse scan
than one that simply requested OCR outright (an image-only upload), and the
escalation itself is evidence of that.
"""

REPAIR_PENALTY_PER_ATTEMPT: Final[float] = 0.85
"""Per attempt beyond the first. Two attempts -> 0.85, three -> 0.7225.

A schema-repair round-trip means the model's first answer was invalid, not
merely imprecise. Compounding the penalty reflects that a document needing two
repairs is more concerning than one needing a single correction.
"""

ARITHMETIC_MISMATCH_PENALTY: Final[float] = 0.75
"""Applied to the whole document when printed totals do not reconcile.

Deliberately document-wide rather than per-field: an arithmetic inconsistency
means at least one of subtotal, tax, or total was misread (or is genuinely
wrong on the source), and which one is not knowable from the mismatch alone.
"""

UNATTRIBUTED_LABEL_PENALTY: Final[float] = 0.95
"""Per aliasable field with no recovered label at all — not even a low-confidence
model-reported one.

A value with a structurally or model-recovered label was read from an
identifiable position on the page; a value with neither rests on the model
having inferred the field's identity by convention alone (per extraction
prompt rule 7), which is inherently a weaker basis than a direct reading.
"""

ARITHMETIC_TOLERANCE: Final[Decimal] = Decimal("0.02")
"""Absolute tolerance, in the invoice's currency, for treating totals as
reconciled. Matches typical sub-cent rounding drift from tax computation, not
a business tolerance — `math_integrity` (FR-4.7) owns the real tolerance
policy; this exists only so trivial rounding does not manufacture a
confidence penalty that the policy engine would not itself raise.
"""


class ConfidenceFactor(BaseModel):
    """One adjustment applied while deriving objective confidence.

    Kept as a list on the result rather than folded silently into a single
    number, so a reviewer — or an eval — can see *why* a score is what it is
    without re-deriving it.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    multiplier: float = Field(gt=0.0, le=1.0)
    reasoning: str


class ObjectiveConfidence(BaseModel):
    """Derived confidence for one extraction, replacing bare model self-report."""

    model_config = ConfigDict(frozen=True)

    model_reported: float = Field(ge=0.0, le=1.0)
    """The floor this derivation starts from: `Invoice.header_confidence`, i.e.
    the model's own minimum across header fields."""

    adjusted: float = Field(ge=0.0, le=1.0)
    """`model_reported` with every applicable penalty multiplied in. This is
    the number that should gate auto-approval, not `model_reported`."""

    factors: tuple[ConfidenceFactor, ...] = ()
    """Empty when nothing pulled the score down — a clean digital-text
    extraction with reconciling totals and fully labelled fields validated in
    one attempt has no factors and `adjusted == model_reported`."""

    @property
    def was_adjusted(self) -> bool:
        return bool(self.factors)


def _reconciles(invoice: Invoice) -> bool | None:
    """Whether printed totals are internally consistent.

    Returns None when there is nothing to check (no tax amount and no lines) —
    absence of evidence is not evidence of a problem, so it must not be scored
    as a mismatch.
    """
    subtotal = invoice.subtotal.value
    total = invoice.total_amount.value
    tax = invoice.tax_amount.value if invoice.tax_amount is not None else None

    checked_any = False
    reconciled = True

    if tax is not None:
        checked_any = True
        expected_total = subtotal + tax
        if expected_total.abs_difference(total).amount > ARITHMETIC_TOLERANCE:
            reconciled = False

    line_sum = invoice.computed_line_sum()
    if line_sum is not None and line_sum.currency == subtotal.currency:
        checked_any = True
        if line_sum.abs_difference(subtotal).amount > ARITHMETIC_TOLERANCE:
            reconciled = False

    if not checked_any:
        return None
    return reconciled


def _unattributed_aliasable_fields(outcome: ExtractionOutcome) -> list[str]:
    """Aliasable header fields present on the invoice with no recovered label.

    Imported lazily to avoid a hard dependency cycle: `mapping.aliases` sits
    above `extract`, and this module only needs the field-name set, not the
    persistence machinery.
    """
    from ap_agent.mapping.aliases import ALIASABLE_FIELDS

    present = set(outcome.raw.header_readings())
    labelled = set(outcome.observed_labels)
    return sorted((present & ALIASABLE_FIELDS) - labelled)


def derive_confidence(outcome: ExtractionOutcome) -> ObjectiveConfidence:
    """Combine model self-report with measurable extraction-quality signals.

    Called once per extraction, immediately after conversion, so the result can
    be attached to the run and surfaced in the UI alongside the raw per-field
    confidences it explains.
    """
    invoice = outcome.invoice
    model_reported = invoice.header_confidence
    score = model_reported
    factors: list[ConfidenceFactor] = []

    if outcome.parse_strategy is ParseStrategy.OCR:
        multiplier = OCR_PENALTY
        reasoning = (
            "Text was reconstructed via OCR rather than read from a digital "
            "text layer, which is lossier by construction."
        )
        if outcome.escalated_to_ocr:
            # Stacked: a document whose structural pass was attempted and
            # rejected as implausible is a worse scan than one that requested
            # OCR outright (e.g. an image-only upload).
            multiplier *= ESCALATED_OCR_PENALTY
            reasoning += (
                " The structural pass was attempted first and rejected as "
                "implausible before OCR ran, indicating a degraded scan."
            )
        score *= multiplier
        factors.append(
            ConfidenceFactor(name="ocr_parse", multiplier=multiplier, reasoning=reasoning)
        )

    if outcome.attempts_used > 1:
        multiplier = REPAIR_PENALTY_PER_ATTEMPT ** (outcome.attempts_used - 1)
        score *= multiplier
        factors.append(
            ConfidenceFactor(
                name="repair_attempts",
                multiplier=multiplier,
                reasoning=(
                    f"The model's first response failed schema validation; a valid "
                    f"reading required {outcome.attempts_used} attempts."
                ),
            )
        )

    reconciles = _reconciles(invoice)
    if reconciles is False:
        score *= ARITHMETIC_MISMATCH_PENALTY
        factors.append(
            ConfidenceFactor(
                name="arithmetic_mismatch",
                multiplier=ARITHMETIC_MISMATCH_PENALTY,
                reasoning=(
                    "Printed totals do not reconcile (subtotal + tax != total, or "
                    "line items do not sum to the printed subtotal), which means "
                    "at least one amount was likely misread."
                ),
            )
        )

    unattributed = _unattributed_aliasable_fields(outcome)
    if unattributed:
        multiplier = UNATTRIBUTED_LABEL_PENALTY ** len(unattributed)
        score *= multiplier
        factors.append(
            ConfidenceFactor(
                name="unattributed_labels",
                multiplier=multiplier,
                reasoning=(
                    f"{len(unattributed)} field(s) have no recovered printed label "
                    f"({', '.join(unattributed)}): their identity rests on model "
                    "inference rather than a readable position on the page."
                ),
            )
        )

    return ObjectiveConfidence(
        model_reported=model_reported,
        adjusted=max(0.0, min(1.0, score)),
        factors=tuple(factors),
    )


__all__ = [
    "ARITHMETIC_TOLERANCE",
    "ConfidenceFactor",
    "ObjectiveConfidence",
    "derive_confidence",
]
