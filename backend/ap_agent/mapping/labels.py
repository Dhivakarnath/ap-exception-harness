"""Recovering the printed label that sits beside an extracted value.

A vendor's own wording for a field — "Grand Total", "Please Remit", "Your Order
No" — is the raw material for alias learning (FR-3.1/3.2). Capture it once, have a
human confirm it, and every later invoice from that vendor is mapped by a
deterministic rule instead of re-inferred. Without the label there is nothing to
learn from and inference runs forever.

The obvious way to get it is to ask the model. That was tried first and measured on
this project's own fixtures across two prompt revisions:

    v1.1.0  strengthened the "do not borrow a column header" rule
            -> model returned null for every label, including labels plainly
               present in the parsed text
    v1.2.0  rewrote rule 5 as a positive obligation, narrowed the null cases
            -> model reported the three totals labels (read from the page image,
               where they exist) but still dropped the five header labels that
               were sitting in the text

Each revision traded one failure for another. That is the signature of asking a
model for something it does not need to guess at: for a header field, the label is
*present in the parse* as a distinct layout element with its own geometry. Reading
it is a spatial lookup, not an inference.

So labels are recovered here, deterministically, and the model's answer is used only
as a supplement — which turns out to be the exactly complementary case. Docling
absorbs the totals block into the line table and drops its labels, and a table
region carries no text of its own, so "Subtotal"/"Tax"/"Total" are unreachable from
the text layer and *only* the model (reading the attached page image) can supply
them. Neither source covers the document alone; together they do.

Recovery produces a **proposal**, never a persisted rule. A proposal is wrong
sometimes — most easily when a parser merges two key-value pairs into one region —
and the human confirmation step in FR-3.2 is what stands between a wrong proposal
and a bad permanent mapping. The `source` and `confidence` on each result exist so
that reviewer sees how the label was obtained rather than being handed a bare
string.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from ap_agent.core.primitives import BoundingBox
from ap_agent.ingest.parsing import ParsedDocument, ParsedRegion

# --- geometric tolerances, in normalised page units (0..1) -------------------
#
# Calibrated against the fixture corpus rather than guessed. On a US-Letter page an
# 11pt line occupies about 0.011 of the page height, and measured label/value pairs
# in the header block sit at an identical vertical band with a horizontal gap of
# 0.109 to 0.136 (labels are ragged-right, values right-aligned).

MAX_SAME_LINE_GAP: Final[float] = 0.22
"""Horizontal gap allowed between a label and the value to its right.

Set above the widest measured pair (0.136) with headroom for wider layouts. Being
generous is safe because candidates compete: the *nearest* qualifying label wins, so
a loose bound admits more candidates rather than choosing a worse one.
"""

MIN_VERTICAL_OVERLAP: Final[float] = 0.5
"""Fraction of the shorter box's height that must overlap to count as one line."""

MAX_STACKED_GAP: Final[float] = 0.030
"""Vertical gap allowed for a label sitting directly above its value.

About two line heights: enough for a label-over-value pair, tight enough to exclude
the previous unrelated row.
"""

MAX_LEFT_EDGE_DRIFT: Final[float] = 0.05
"""Left-edge misalignment allowed for a stacked label/value pair."""

# --- label shape -------------------------------------------------------------

MAX_LABEL_CHARS: Final[int] = 48
MAX_LABEL_WORDS: Final[int] = 6
MAX_LABEL_DIGIT_RATIO: Final[float] = 0.4

_CURRENCY_CODE_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z]{3}")


class LabelSource(StrEnum):
    """How a label was obtained. Shown to the reviewer confirming an alias."""

    SAME_REGION = "same_region"
    """Label and value in one region: "Invoice Number: INV-60000"."""

    ADJACENT_LEFT = "adjacent_left"
    """Separate region on the same line, to the left. The common header layout."""

    ADJACENT_ABOVE = "adjacent_above"
    """Separate region directly above. Used by narrow and stacked layouts."""

    READING_ORDER = "reading_order"
    """Preceding element in reading order, with no geometry to confirm it.

    Weakest structural source: without boxes there is no way to tell a label above
    its value from an unrelated value in an adjacent column.
    """

    MODEL_REPORTED = "model_reported"
    """The model read it, and the text layer cannot corroborate it.

    Not inherently wrong — it is the only way to recover a label that exists solely
    inside a page image, which is the case for the totals block — but it is
    unverifiable here, so it ranks below every structural source.
    """


# Ordered best-first. Determines which candidate wins when several are available.
_SOURCE_CONFIDENCE: Final[dict[LabelSource, float]] = {
    LabelSource.SAME_REGION: 0.95,
    LabelSource.ADJACENT_LEFT: 0.90,
    LabelSource.ADJACENT_ABOVE: 0.80,
    LabelSource.READING_ORDER: 0.55,
    LabelSource.MODEL_REPORTED: 0.65,
}

# A region holding several colon-separated pairs ("Bank: First National Account
# ending: 4821") cannot be split reliably into label and value, so a label taken
# from one is downgraded rather than trusted.
_AMBIGUOUS_SAME_REGION_CONFIDENCE: Final[float] = 0.70


class RecoveredLabel(BaseModel):
    """A printed label proposed for an extracted value."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    """The label verbatim, including any trailing colon.

    Kept verbatim because the alias table matches on what the vendor actually
    prints. Normalisation happens at comparison time, not at capture time, so the
    original is still visible to whoever confirms the rule.
    """

    source: LabelSource
    confidence: float = Field(ge=0.0, le=1.0)
    """Confidence in the *attribution*, not in the value.

    A label can be read perfectly and still be attached to the wrong field, which is
    the failure this number describes.
    """

    element_ref: str | None = None
    """Region the label came from, so the UI can highlight it. Null when model-reported."""

    @property
    def normalised(self) -> str:
        """Comparison form: casefolded, trailing punctuation and inner runs collapsed.

        `"Grand Total:"`, `"grand  total"`, and `"GRAND TOTAL"` all reduce to
        `"grand total"` so the alias table does not accumulate one row per
        capitalisation.
        """
        return normalise_label_text(self.text)


def normalise_label_text(text: str) -> str:
    """Comparison form for a bare label string.

    The free function `RecoveredLabel.normalised` delegates to, so callers
    holding only a string (looking a label up against the alias table, for
    instance) do not need to construct a `RecoveredLabel` to get the same
    normalisation.
    """
    return " ".join(text.strip().rstrip(":-–—= \t").casefold().split())


def is_label_shaped(text: str) -> bool:
    """Whether a string could plausibly be a field label.

    A filter against the main false-positive risk: attaching *another field's value*
    as a label. Labels are short, mostly alphabetic noun phrases; values are numbers,
    dates, codes, and addresses. Rejecting on shape removes most confusions before
    geometry is even considered.
    """
    stripped = text.strip().rstrip(":")
    if not stripped or len(stripped) > MAX_LABEL_CHARS:
        return False
    if not any(char.isalpha() for char in stripped):
        # Pure numbers, dates, and amounts.
        return False
    if len(stripped.split()) > MAX_LABEL_WORDS:
        # A sentence or an address line, not a label.
        return False

    # Codes like "PO-2001" or "INV-60000" contain letters but read as values, so a
    # high digit density disqualifies a candidate even when it is short and worded.
    digits = sum(1 for char in stripped if char.isdigit())
    if digits / len(stripped) > MAX_LABEL_DIGIT_RATIO:
        return False

    # A bare three-letter all-caps token is an ISO currency code (USD, EUR, JPY...),
    # not a label. Observed on a degraded OCR pass: the parse merged "Subtotal: USD
    # 120.00" and lost "Subtotal", leaving "USD:" adjacent to the amount — shaped
    # like a label by every other test, but a currency code, not a field name.
    return not _CURRENCY_CODE_RE.fullmatch(stripped)


# --- geometry ----------------------------------------------------------------


def _vertical_overlap_ratio(first: BoundingBox, second: BoundingBox) -> float:
    """Shared vertical extent as a fraction of the shorter box's height."""
    overlap = min(first.bottom, second.bottom) - max(first.top, second.top)
    if overlap <= 0:
        return 0.0
    shortest = min(first.bottom - first.top, second.bottom - second.top)
    if shortest <= 0:
        return 0.0
    return overlap / shortest


def _is_left_of_on_same_line(label: BoundingBox, value: BoundingBox) -> float | None:
    """Gap between a label and a value on the same line, or None if not that pair."""
    if _vertical_overlap_ratio(label, value) < MIN_VERTICAL_OVERLAP:
        return None
    gap = value.left - label.right
    if gap < 0 or gap > MAX_SAME_LINE_GAP:
        return None
    return gap


def _is_directly_above(label: BoundingBox, value: BoundingBox) -> float | None:
    """Vertical gap for a label stacked above a value, or None if not that pair."""
    gap = value.top - label.bottom
    if gap < 0 or gap > MAX_STACKED_GAP:
        return None

    left_aligned = abs(label.left - value.left) <= MAX_LEFT_EDGE_DRIFT
    horizontally_overlapping = (
        min(label.right, value.right) - max(label.left, value.left)
    ) > 0
    if not (left_aligned or horizontally_overlapping):
        return None
    return gap


# --- recovery ----------------------------------------------------------------


def _value_regions(parsed: ParsedDocument, value: str) -> list[ParsedRegion]:
    """Regions containing the value, tightest match first.

    Ordering by text length matters: an amount appears both in its own small region
    and inside any paragraph that quotes it, and the small region is the one whose
    geometry describes where the value actually sits.
    """
    target = value.strip().casefold()
    if not target:
        return []
    matches = [r for r in parsed.regions if r.text and target in r.text.casefold()]
    return sorted(matches, key=lambda r: len(r.text))


def _strip_leaked_value_tokens(candidate: str) -> str:
    """Drop a leaked prior value from the front of a candidate label.

    OCR frequently merges several "Label: value" pairs into one region with no
    punctuation between a value and the next label: "Invoice Number: INV-60000
    Invoice Date:" contains the true label "Invoice Date" preceded by the previous
    field's leaked value. A value token reads as a word containing a digit (an
    amount, code, or date component); a label does not. Dropping everything up to
    and including the last such token removes the leak while leaving the label
    intact. Observed on the fixture set with OCR-merged headers; see INC-004.
    """
    words = candidate.split()
    last_value_token = max(
        (i for i, w in enumerate(words) if any(c.isdigit() for c in w)), default=-1
    )
    return " ".join(words[last_value_token + 1 :])


def _from_same_region(region: ParsedRegion, value: str) -> RecoveredLabel | None:
    """Split "Label: value" within a single region."""
    text = region.text
    index = text.casefold().find(value.strip().casefold())
    if index <= 0:
        return None

    prefix = text[:index].strip()
    if not prefix:
        return None

    # Several pairs merged into one region. The colon immediately before the value
    # is not necessarily the boundary of the label: "Tax: USD 9.90" has a bare
    # currency token between the colon and the amount, so the segment right before
    # the value is "USD", not "Tax". Walk backward through colon-delimited
    # segments, cleaning each of a leaked prior value, until one is label-shaped.
    segments = [s.strip() for s in prefix.split(":") if s.strip()]
    ambiguous = len(segments) > 1

    candidate = ""
    for raw_segment in reversed(segments):
        cleaned = _strip_leaked_value_tokens(raw_segment)
        words = cleaned.split()
        if len(words) > MAX_LABEL_WORDS:
            cleaned = " ".join(words[-MAX_LABEL_WORDS:])
        if is_label_shaped(cleaned):
            candidate = cleaned
            break

    if not candidate:
        return None

    # Restore the colon the split consumed, so the stored label matches the page.
    text_out = candidate if candidate.endswith(":") else f"{candidate}:"
    return RecoveredLabel(
        text=text_out,
        source=LabelSource.SAME_REGION,
        confidence=(
            _AMBIGUOUS_SAME_REGION_CONFIDENCE
            if ambiguous
            else _SOURCE_CONFIDENCE[LabelSource.SAME_REGION]
        ),
        element_ref=region.element_ref,
    )


def _from_geometry(
    parsed: ParsedDocument, value_region: ParsedRegion
) -> RecoveredLabel | None:
    """Nearest label-shaped region to the left of, or directly above, the value."""
    value_box = value_region.bbox
    if value_box is None:
        return None

    best: tuple[int, float, LabelSource, ParsedRegion] | None = None

    for region in parsed.regions:
        if region.element_ref == value_region.element_ref or region.bbox is None:
            continue
        if not region.text.strip() or not is_label_shaped(region.text):
            continue

        gap = _is_left_of_on_same_line(region.bbox, value_box)
        source = LabelSource.ADJACENT_LEFT
        if gap is None:
            gap = _is_directly_above(region.bbox, value_box)
            source = LabelSource.ADJACENT_ABOVE
        if gap is None:
            continue

        # Same-line beats stacked outright (tier), regardless of which gap is
        # numerically smaller: reading left-to-right is the stronger convention for
        # a header key-value pair, and a stacked candidate is more easily an
        # unrelated heading sitting close above (observed: a page-level "INVOICE"
        # title sits closer above "INV-60000" than "Invoice Number:" sits to its
        # left). Gap only breaks ties within the same tier.
        tier = 0 if source is LabelSource.ADJACENT_LEFT else 1
        rank = (tier, gap, source, region)
        if best is None or rank[:2] < best[:2]:
            best = rank

    if best is None:
        return None

    _, _, source, region = best
    return RecoveredLabel(
        text=region.text.strip(),
        source=source,
        confidence=_SOURCE_CONFIDENCE[source],
        element_ref=region.element_ref,
    )


def _from_reading_order(
    parsed: ParsedDocument, value_region: ParsedRegion
) -> RecoveredLabel | None:
    """Preceding element in reading order, when no geometry is available.

    Only reached on parses without bounding boxes. Deliberately low confidence: with
    no geometry there is no way to distinguish a label above its value from an
    unrelated value in a neighbouring column, and column-major layouts break this
    assumption entirely.
    """
    preceding = [r for r in parsed.regions if r.reading_order < value_region.reading_order]
    if not preceding:
        return None

    candidate = max(preceding, key=lambda r: r.reading_order)
    if not candidate.text.strip() or not is_label_shaped(candidate.text):
        return None

    return RecoveredLabel(
        text=candidate.text.strip(),
        source=LabelSource.READING_ORDER,
        confidence=_SOURCE_CONFIDENCE[LabelSource.READING_ORDER],
        element_ref=candidate.element_ref,
    )


def recover_label(parsed: ParsedDocument, value: str) -> RecoveredLabel | None:
    """Best structural label for `value`, or None if the parse does not support one.

    Returning None is a real answer, not a failure: a letterhead vendor name has no
    label, and inventing one would seed the alias table with noise.
    """
    if not value.strip():
        return None

    for region in _value_regions(parsed, value):
        found = _from_same_region(region, value) or _from_geometry(parsed, region)
        if found is not None:
            return found

    # Geometry exhausted. Fall back to reading order only where there was no
    # geometry to use in the first place.
    for region in _value_regions(parsed, value):
        if region.bbox is None:
            found = _from_reading_order(parsed, region)
            if found is not None:
                return found
    return None


def resolve_label(
    parsed: ParsedDocument, value: str, model_reported: str | None
) -> RecoveredLabel | None:
    """Reconcile the structural label with the model's, preferring the verifiable one.

    Structure wins when it produces an answer: it is exact and reproducible, whereas
    the model's answer varies between calls and between prompt versions. The model
    fills the gap that structure cannot reach — a label that exists only inside a
    page image, or inside a table region that carries no text of its own.
    """
    if not value.strip():
        return None

    structural = recover_label(parsed, value)
    if structural is not None:
        return structural

    reported = (model_reported or "").strip()
    if not reported or not is_label_shaped(reported):
        return None

    # Reject an echo of the value itself. Observed on a degraded scan: the model
    # reported a letterhead vendor name as both the value and its own
    # `printed_label`, presumably because the name visually reads as a heading. A
    # field cannot be its own label.
    if reported.casefold() == value.strip().casefold():
        return None

    return RecoveredLabel(
        text=reported,
        source=LabelSource.MODEL_REPORTED,
        confidence=_SOURCE_CONFIDENCE[LabelSource.MODEL_REPORTED],
    )
