"""Docling parsing: structure, provenance, and the two-pass OCR strategy.

Marked `integration` because Docling loads layout models (and downloads OCR
weights on first use), which is too slow and too network-dependent for the unit
tier.

Requires the generated dataset. Run `make dataset-quick` first.

The assertions that matter most:

* **Bbox inversion.** Docling reports PDF boxes bottom-left origin; we normalise to
  top-left for the browser overlay. Getting this backwards would put every
  highlight on the wrong part of the page — wrong in a way only a human looking at
  the UI would notice, so it is pinned by a test.
* **OCR escalation.** A scanned PDF parsed without OCR returns an *empty parse with
  a success status*, which is the most dangerous possible outcome: it looks like a
  document with no content rather than one we failed to read.
* **Table preservation.** Flattening the line-item table destroys the meaning of a
  quantity, which is only interpretable relative to its column.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ap_agent.core.primitives import RegionKind
from ap_agent.errors import ParsingError
from ap_agent.ingest.events import DocumentPayload
from ap_agent.ingest.parsing import (
    MIN_PLAUSIBLE_TEXT_CHARS,
    ParsedDocument,
    ParseStrategy,
    parse_path,
    parse_payload,
    regions_to_json,
)

pytestmark = pytest.mark.integration

INVOICES = Path(__file__).resolve().parents[3] / "datasets" / "generated" / "invoices"

TEXT_PDF = "clean_touchless-000.pdf"
SCANNED_PDF = "ocr_noise-000.pdf"
IMAGE_ONLY = "image_only-000.png"


def _require(name: str) -> Path:
    path = INVOICES / name
    if not path.is_file():
        pytest.skip(f"{name} missing — run `make dataset-quick` to generate fixtures.")
    return path


@pytest.fixture(scope="module")
def text_doc() -> ParsedDocument:
    return parse_path(_require(TEXT_PDF), media_type="application/pdf")


@pytest.fixture(scope="module")
def scanned_doc() -> ParsedDocument:
    return parse_path(_require(SCANNED_PDF), media_type="application/pdf")


@pytest.fixture(scope="module")
def image_doc() -> ParsedDocument:
    return parse_path(_require(IMAGE_ONLY), media_type="image/png")


class TestStructuralPass:
    def test_text_pdf_uses_the_cheap_path(self, text_doc: ParsedDocument) -> None:
        # OCR costs 2-4x and adds nothing when a text layer exists.
        assert text_doc.strategy is ParseStrategy.STRUCTURAL
        assert text_doc.escalated_to_ocr is False

    def test_recovers_the_document_content(self, text_doc: ParsedDocument) -> None:
        assert text_doc.page_count == 1
        assert text_doc.text_length > MIN_PLAUSIBLE_TEXT_CHARS
        for expected in ("Acme Corporation", "INV-60000", "PO-2001", "259.80"):
            assert expected in text_doc.markdown, f"{expected!r} missing from parse"

    def test_records_parser_identity(self, text_doc: ParsedDocument) -> None:
        assert text_doc.parser_name == "docling"
        assert text_doc.parser_version != "unknown"
        assert text_doc.duration_ms > 0


class TestRegionProvenance:
    def test_regions_are_detected_with_reading_order(self, text_doc: ParsedDocument) -> None:
        assert len(text_doc.regions) > 5
        orders = [r.reading_order for r in text_doc.regions]
        assert orders == sorted(orders), "reading order must be monotonic"

    def test_bbox_is_normalised_to_the_unit_square(self, text_doc: ParsedDocument) -> None:
        boxed = [r for r in text_doc.regions if r.bbox is not None]
        assert boxed, "no region carried a bounding box"
        for region in boxed:
            box = region.bbox
            assert box is not None
            for value in (box.left, box.top, box.right, box.bottom):
                assert 0.0 <= value <= 1.0
            assert box.left <= box.right
            assert box.top <= box.bottom

    def test_bbox_vertical_orientation_is_top_left(self, text_doc: ParsedDocument) -> None:
        # The inversion test. Docling reports bottom-left origin; if we failed to
        # flip it, the vendor name printed at the top of the page would appear
        # *below* the remittance block printed near the bottom.
        header = text_doc.find_region_containing("Acme Corporation")
        footer = text_doc.find_region_containing("Account ending")

        assert header is not None and header.bbox is not None
        assert footer is not None and footer.bbox is not None
        assert header.bbox.top < footer.bbox.top, (
            "page header must normalise above the remittance footer — "
            "bottom-left to top-left inversion is wrong"
        )
        # The header sits in the top fifth of the page.
        assert header.bbox.top < 0.2

    def test_region_links_resolve(self, text_doc: ParsedDocument) -> None:
        # This is what makes UI highlight-back possible (FR-2.5).
        target = text_doc.find_region_containing("INV-60000")
        assert target is not None
        resolved = text_doc.region_by_ref(target.element_ref)
        assert resolved is not None
        assert resolved.element_ref == target.element_ref
        assert "INV-60000" in resolved.text

    def test_unknown_ref_resolves_to_none(self, text_doc: ParsedDocument) -> None:
        assert text_doc.region_by_ref("#/texts/99999") is None

    def test_element_refs_are_unique(self, text_doc: ParsedDocument) -> None:
        refs = [r.element_ref for r in text_doc.regions]
        assert len(refs) == len(set(refs))

    def test_region_converts_to_canonical_source_region(
        self, text_doc: ParsedDocument
    ) -> None:
        region = next(r for r in text_doc.regions if r.text)
        source = region.to_source_region()
        assert source.page == region.page
        assert source.element_ref == region.element_ref
        assert source.kind is region.kind
        assert source.snippet is not None

    def test_regions_serialise_for_persistence(self, text_doc: ParsedDocument) -> None:
        # Persisted so the UI can highlight on a completed run without a
        # re-parse, which would cost seconds and could yield different boxes.
        payload = regions_to_json(text_doc.regions)
        assert len(payload) == len(text_doc.regions)
        assert {"element_ref", "kind", "page", "bbox", "text", "reading_order"} <= set(
            payload[0]
        )


class TestTablePreservation:
    def test_line_item_table_is_detected(self, text_doc: ParsedDocument) -> None:
        assert text_doc.tables, "the line-item table was not detected"
        assert text_doc.table_regions

    def test_table_keeps_its_grid_shape(self, text_doc: ParsedDocument) -> None:
        table = text_doc.tables[0]
        assert table.num_cols >= 4
        assert table.num_rows >= 1
        assert table.is_empty is False

    def test_table_header_names_the_columns(self, text_doc: ParsedDocument) -> None:
        # A quantity is only interpretable relative to its column, so losing the
        # header would make the line items unusable.
        header = [h.casefold() for h in text_doc.tables[0].header]
        assert any("description" in h for h in header)
        assert any("qty" in h or "quantity" in h for h in header)

    def test_line_values_survive_in_their_own_cells(self, text_doc: ParsedDocument) -> None:
        rows = text_doc.tables[0].rows
        joined = [" | ".join(row) for row in rows]
        assert any("Steel bracket" in row for row in joined)
        # Quantity and amount are in separate cells, not merged into one string.
        first = next(row for row in rows if "Steel bracket" in " ".join(row))
        assert any("20" in cell for cell in first)
        assert any("240.00" in cell for cell in first)


class TestPictureDetection:
    def test_picture_regions_are_exposed(self, text_doc: ParsedDocument) -> None:
        # Passed to the multimodal extractor so a total that only appears inside
        # an image is still readable (FR-2.2).
        pictures = text_doc.picture_regions
        assert pictures, "no picture region detected"
        assert all(r.kind is RegionKind.PICTURE for r in pictures)


class TestOcrEscalation:
    def test_scanned_pdf_escalates_to_ocr(self, scanned_doc: ParsedDocument) -> None:
        # Without escalation this document parses to zero items *with a success
        # status* — an empty parse that looks like a contentless document.
        assert scanned_doc.strategy is ParseStrategy.OCR
        assert scanned_doc.escalated_to_ocr is True

    def test_scanned_pdf_recovers_some_text(self, scanned_doc: ParsedDocument) -> None:
        # A degraded scan is lossy by design; we assert recovery, not fidelity.
        assert scanned_doc.text_length > 0
        assert scanned_doc.regions

    def test_image_goes_straight_to_ocr_without_escalating(
        self, image_doc: ParsedDocument
    ) -> None:
        # An image has no text layer by construction, so a structural attempt
        # would be a guaranteed waste. `escalated_to_ocr` stays False because
        # nothing was escalated *from*.
        assert image_doc.strategy is ParseStrategy.OCR
        assert image_doc.escalated_to_ocr is False
        assert image_doc.text_length > 0

    def test_degraded_scan_yields_less_than_the_digital_original(
        self, text_doc: ParsedDocument, scanned_doc: ParsedDocument
    ) -> None:
        # Confirms the fixtures are genuinely degraded rather than nominally so.
        # This is why extraction confidence must gate auto-approval.
        assert scanned_doc.text_length < text_doc.text_length


class TestFailureBehaviour:
    def test_unreadable_pdf_fails_loudly(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 this is not a real pdf body")
        with pytest.raises(ParsingError):
            parse_path(broken, media_type="application/pdf")

    def test_blank_page_is_refused_rather_than_returned_empty(self, tmp_path: Path) -> None:
        # An empty parse reported as success is the outcome most likely to cause a
        # wrong decision downstream, so it must never be returned.
        from PIL import Image

        blank = tmp_path / "blank.png"
        Image.new("L", (1275, 1650), color=255).save(blank)

        with pytest.raises(ParsingError, match="no text and no layout regions"):
            parse_path(blank, media_type="image/png")


class TestParseFromBytes:
    def test_payload_parsing_matches_path_parsing(self) -> None:
        path = _require(TEXT_PDF)
        payload = DocumentPayload(
            filename=TEXT_PDF, media_type="application/pdf", content=path.read_bytes()
        )
        from_bytes = parse_payload(payload, document_id="doc-1")

        assert from_bytes.document_id == "doc-1"
        assert from_bytes.strategy is ParseStrategy.STRUCTURAL
        assert "INV-60000" in from_bytes.markdown
