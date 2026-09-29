"""Structural label recovery (`mapping.labels`).

Constructs `ParsedDocument`/`ParsedRegion` fixtures directly rather than
running Docling, so these are true unit tests: fast, and pinned exactly to the
geometry cases this module exists to handle. The real-document coverage lives
in `scripts/extract_demo.py`, exercised manually against the generated
dataset and against live Bedrock (see INC-004).
"""

from __future__ import annotations

from ap_agent.core.primitives import BoundingBox, RegionKind
from ap_agent.ingest.parsing import ParsedDocument, ParsedRegion, ParseStrategy
from ap_agent.mapping.labels import (
    LabelSource,
    is_label_shaped,
    normalise_label_text,
    recover_label,
    resolve_label,
)


def _region(
    order: int,
    text: str,
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
    ref: str | None = None,
) -> ParsedRegion:
    return ParsedRegion(
        element_ref=ref or f"#/texts/{order}",
        kind=RegionKind.TEXT,
        page=1,
        bbox=BoundingBox(left=left, top=top, right=right, bottom=bottom),
        text=text,
        reading_order=order,
    )


def _doc(regions: list[ParsedRegion]) -> ParsedDocument:
    markdown = "\n".join(r.text for r in regions)
    return ParsedDocument(
        page_count=1,
        strategy=ParseStrategy.STRUCTURAL,
        parser_version="test",
        markdown=markdown,
        regions=tuple(regions),
        tables=(),
        duration_ms=1.0,
    )


class TestIsLabelShaped:
    def test_accepts_a_short_worded_label(self) -> None:
        assert is_label_shaped("Invoice Number:") is True

    def test_rejects_a_pure_amount(self) -> None:
        assert is_label_shaped("259.80") is False

    def test_rejects_a_code_with_high_digit_density(self) -> None:
        assert is_label_shaped("INV-60000") is False

    def test_rejects_a_long_sentence(self) -> None:
        assert (
            is_label_shaped("This is much too long and wordy to be a field label")
            is False
        )

    def test_rejects_a_bare_currency_code(self) -> None:
        # Docling can merge "Subtotal: USD 120.00" and drop "Subtotal", leaving
        # "USD:" adjacent to the amount — shaped like a label by every other
        # test, but a currency code, not a field name.
        assert is_label_shaped("USD:") is False
        assert is_label_shaped("EUR") is False

    def test_rejects_empty_string(self) -> None:
        assert is_label_shaped("") is False

    def test_accepts_a_label_with_moderate_digits(self) -> None:
        # A label like "Terms (Net 30)" should not be excluded purely for
        # containing a couple of digits.
        assert is_label_shaped("Payment Terms") is True


class TestNormalisation:
    def test_strips_trailing_colon_and_casefolds(self) -> None:
        assert normalise_label_text("Grand Total:") == "grand total"

    def test_collapses_whitespace(self) -> None:
        assert normalise_label_text("Grand   Total") == "grand total"

    def test_variants_converge(self) -> None:
        assert normalise_label_text("GRAND TOTAL") == normalise_label_text("grand total:")


class TestSameLineRecovery:
    def test_label_left_of_value_on_same_line(self) -> None:
        # Matches the geometry measured on the fixture PDF: "Invoice Number:"
        # at l=0.625..0.732, "INV-60000" at l=0.841..0.912, same vertical band.
        label = _region(0, "Invoice Number:", left=0.625, top=0.103, right=0.732, bottom=0.113)
        value = _region(1, "INV-60000", left=0.841, top=0.103, right=0.912, bottom=0.113)
        doc = _doc([label, value])

        found = recover_label(doc, "INV-60000")
        assert found is not None
        assert found.text == "Invoice Number:"
        assert found.source is LabelSource.ADJACENT_LEFT

    def test_too_wide_a_gap_is_not_treated_as_a_pair(self) -> None:
        label = _region(0, "Invoice Number:", left=0.0, top=0.1, right=0.1, bottom=0.11)
        value = _region(1, "INV-60000", left=0.9, top=0.1, right=0.99, bottom=0.11)
        doc = _doc([label, value])

        assert recover_label(doc, "INV-60000") is None

    def test_no_vertical_overlap_is_not_treated_as_same_line(self) -> None:
        label = _region(0, "Invoice Number:", left=0.1, top=0.1, right=0.2, bottom=0.11)
        value = _region(1, "INV-60000", left=0.25, top=0.5, right=0.35, bottom=0.51)
        doc = _doc([label, value])

        assert recover_label(doc, "INV-60000") is None


class TestStackedRecovery:
    def test_label_directly_above_value(self) -> None:
        label = _region(0, "INVOICE", left=0.764, top=0.048, right=0.912, bottom=0.074)
        value = _region(1, "INV-60000", left=0.760, top=0.090, right=0.912, bottom=0.100)
        doc = _doc([label, value])

        found = recover_label(doc, "INV-60000")
        assert found is not None
        assert found.source is LabelSource.ADJACENT_ABOVE

    def test_same_line_wins_over_stacked_even_at_a_larger_gap(self) -> None:
        # A page-level title sitting close above a value must not beat a true
        # key-value label sitting further away on the same line: same-line is a
        # stronger convention for a header field than proximity above.
        title = _region(0, "INVOICE", left=0.760, top=0.048, right=0.912, bottom=0.074)
        real_label = _region(
            1, "Invoice Number:", left=0.625, top=0.103, right=0.732, bottom=0.113
        )
        value = _region(2, "INV-60000", left=0.841, top=0.103, right=0.912, bottom=0.113)
        doc = _doc([title, real_label, value])

        found = recover_label(doc, "INV-60000")
        assert found is not None
        assert found.text == "Invoice Number:"
        assert found.source is LabelSource.ADJACENT_LEFT


class TestSameRegionRecovery:
    def test_splits_label_and_value_in_one_region(self) -> None:
        region = _region(
            0, "Invoice Number: INV-60000", left=0.1, top=0.1, right=0.5, bottom=0.11
        )
        doc = _doc([region])

        found = recover_label(doc, "INV-60000")
        assert found is not None
        assert found.text == "Invoice Number:"
        assert found.source is LabelSource.SAME_REGION

    def test_strips_a_leaked_prior_value_from_the_segment(self) -> None:
        # OCR merged three key-value pairs with no punctuation between the
        # value and the next label.
        region = _region(
            0,
            "Invoice Number: INV-60000 Invoice Date: 2026-02-27 PO Number: PO-2001",
            left=0.1,
            top=0.1,
            right=0.9,
            bottom=0.11,
        )
        doc = _doc([region])

        found = recover_label(doc, "2026-02-27")
        assert found is not None
        assert found.text == "Invoice Date:"

    def test_currency_token_between_colon_and_amount_is_skipped(self) -> None:
        # "Tax: USD 9.90" - the segment immediately before the value is "USD",
        # which must be rejected so the walk continues back to "Tax".
        region = _region(
            0,
            "Subtotal: USD 120.00 Tax: USD 9.90 Total: USD 129.90",
            left=0.1,
            top=0.1,
            right=0.9,
            bottom=0.11,
        )
        doc = _doc([region])

        found = recover_label(doc, "9.90")
        assert found is not None
        assert found.text == "Tax:"

    def test_value_not_present_in_any_region_returns_none(self) -> None:
        region = _region(0, "Some unrelated text", left=0.1, top=0.1, right=0.5, bottom=0.11)
        doc = _doc([region])

        assert recover_label(doc, "nowhere-to-be-found") is None


class TestResolveLabel:
    def test_structural_result_wins_over_model_reported(self) -> None:
        label = _region(0, "Invoice Number:", left=0.625, top=0.103, right=0.732, bottom=0.113)
        value = _region(1, "INV-60000", left=0.841, top=0.103, right=0.912, bottom=0.113)
        doc = _doc([label, value])

        found = resolve_label(doc, "INV-60000", model_reported="Invoice No")
        assert found is not None
        assert found.text == "Invoice Number:"
        assert found.source is LabelSource.ADJACENT_LEFT

    def test_falls_back_to_model_reported_when_structure_has_nothing(self) -> None:
        # Simulates the totals-block case: the value exists nowhere in the
        # text-derived regions (it was absorbed into a table Docling could not
        # export), so structural recovery has no region to search.
        doc = _doc([])

        found = resolve_label(doc, "259.80", model_reported="Total:")
        assert found is not None
        assert found.text == "Total:"
        assert found.source is LabelSource.MODEL_REPORTED

    def test_rejects_a_model_reported_label_that_echoes_the_value(self) -> None:
        # Observed on a degraded scan: the model reported a letterhead vendor
        # name as both the value and its own printed_label.
        doc = _doc([])
        found = resolve_label(doc, "Acme Corporation", model_reported="Acme Corporation")
        assert found is None

    def test_rejects_a_model_reported_label_that_is_not_label_shaped(self) -> None:
        doc = _doc([])
        found = resolve_label(doc, "259.80", model_reported="259.80")
        assert found is None

    def test_returns_none_when_neither_source_has_an_answer(self) -> None:
        doc = _doc([])
        assert resolve_label(doc, "259.80", model_reported=None) is None

    def test_returns_none_for_a_blank_value(self) -> None:
        doc = _doc([])
        assert resolve_label(doc, "   ", model_reported="Total:") is None
