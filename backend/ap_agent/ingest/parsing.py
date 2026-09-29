"""Document parsing with Docling.

Docling is chosen over flat OCR because invoices are *structured* data trapped in
pixels: line-item tables, key-value header pairs, totals. A flat text dump merges
table columns and separates a label from its value, which is precisely the
information the extraction step needs (ADR-012).

Two design decisions here are load-bearing.

**Two-pass OCR, not OCR-always.** Measured on this project's own fixtures:

    text PDF,  OCR off ->  1.5s, 745 chars, 20 items
    text PDF,  OCR on  ->  3.5s, 744 chars   (2.3x cost, no benefit)
    scanned,   OCR off ->  0.5s,   0 chars,  0 items   (silently empty!)
    scanned,   OCR on  ->  6.1s, 222 chars,  9 items

So OCR-always wastes 2-4x on the common case, and OCR-never returns an *empty
parse with a success status* on scanned input — the worst outcome, because an
empty parse looks like a valid document with no content. We therefore attempt the
cheap structural pass first and escalate to OCR only when the yield is
implausibly low. Images skip straight to OCR since they have no text layer by
definition.

**Region provenance is retained.** Docling gives every item a `self_ref` and a
bounding box. Keeping both is what lets the UI highlight the exact spot an
extracted field came from (FR-2.5), turning extraction from an assertion into
something a reviewer can check.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ap_agent.core.primitives import BoundingBox, RegionKind, SourceRegion
from ap_agent.errors import ErrorContext, ParsingError
from ap_agent.ingest.events import DocumentPayload

logger = logging.getLogger(__name__)

# Below this many characters a structural pass is treated as having failed to
# read the document, and OCR is attempted. A real invoice has a vendor name,
# numbers, dates and totals; 120 characters cannot represent one. Set from the
# fixture measurements above, where a genuine parse yields ~750 characters and a
# text-layer-less scan yields 0.
MIN_PLAUSIBLE_TEXT_CHARS = 120

# Docling layout labels -> our RegionKind. Unmapped labels become UNKNOWN rather
# than raising: a new Docling label should not break ingestion, it should just be
# less precisely described.
_LABEL_TO_REGION_KIND: dict[str, RegionKind] = {
    "text": RegionKind.TEXT,
    "paragraph": RegionKind.TEXT,
    "table": RegionKind.TABLE,
    "picture": RegionKind.PICTURE,
    "figure": RegionKind.PICTURE,
    "title": RegionKind.TITLE,
    "section_header": RegionKind.SECTION_HEADER,
    "page_header": RegionKind.PAGE_HEADER,
    "page_footer": RegionKind.PAGE_FOOTER,
    "list_item": RegionKind.LIST_ITEM,
    "caption": RegionKind.CAPTION,
    "formula": RegionKind.FORMULA,
    "footnote": RegionKind.FOOTNOTE,
}


class ParseStrategy(StrEnum):
    """Which pass produced the result. Recorded for cost attribution and the UI."""

    STRUCTURAL = "structural"
    """Text layer read directly. Cheap, and the desired path."""

    OCR = "ocr"
    """Rasterised and recognised. Slower and lossier; used when necessary."""


class ParsedRegion(BaseModel):
    """One layout element, with enough provenance to point at it later."""

    model_config = ConfigDict(frozen=True)

    element_ref: str
    """Docling's stable handle, e.g. `#/texts/4`. The link target for highlighting."""

    kind: RegionKind
    page: int = Field(ge=1)
    bbox: BoundingBox | None
    text: str = ""
    reading_order: int = Field(ge=0)

    def to_source_region(self) -> SourceRegion:
        """Convert to the canonical provenance type used by `Extracted[T]`."""
        return SourceRegion(
            page=self.page,
            kind=self.kind,
            bbox=self.bbox,
            element_ref=self.element_ref,
            snippet=self.text[:500] or None,
        )


class ParsedTable(BaseModel):
    """A detected table, preserved as a grid.

    Kept as rows-and-cells rather than flattened text: the line-item table is the
    part of an invoice where flattening destroys the meaning, since a quantity is
    only interpretable relative to its column.
    """

    model_config = ConfigDict(frozen=True)

    element_ref: str
    page: int = Field(ge=1)
    bbox: BoundingBox | None
    num_rows: int = Field(ge=0)
    num_cols: int = Field(ge=0)
    header: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.num_rows == 0 or self.num_cols == 0


class ParsedDocument(BaseModel):
    """The structural result of parsing one document.

    Carries markdown (for the model), regions (for provenance and highlighting),
    and tables (for line items) — three views of the same parse, each needed by a
    different consumer.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str | None = None
    page_count: int = Field(ge=0)
    strategy: ParseStrategy
    parser_name: str = "docling"
    parser_version: str

    markdown: str
    regions: tuple[ParsedRegion, ...]
    tables: tuple[ParsedTable, ...]
    duration_ms: float = Field(ge=0)

    # True when a structural pass was attempted and rejected as implausible. Kept
    # because "we had to fall back to OCR" is a useful signal about a vendor's
    # document quality over time.
    escalated_to_ocr: bool = False

    @property
    def text_length(self) -> int:
        return len(self.markdown.strip())

    @property
    def picture_regions(self) -> tuple[ParsedRegion, ...]:
        """Embedded images: logos, stamps, signatures (FR-2.2).

        Passed to the multimodal extractor alongside the parsed structure, so an
        invoice whose total only appears inside an image is still readable.
        """
        return tuple(r for r in self.regions if r.kind is RegionKind.PICTURE)

    @property
    def table_regions(self) -> tuple[ParsedRegion, ...]:
        return tuple(r for r in self.regions if r.kind is RegionKind.TABLE)

    def region_by_ref(self, element_ref: str) -> ParsedRegion | None:
        """Resolve a link back to its region. Backs UI highlight-back."""
        return next((r for r in self.regions if r.element_ref == element_ref), None)

    def find_region_containing(self, needle: str) -> ParsedRegion | None:
        """First region whose text contains `needle`.

        Used to attach provenance to an extracted value when the extractor reports
        a value but not the element it came from.
        """
        if not needle:
            return None
        target = needle.casefold()
        return next((r for r in self.regions if target in r.text.casefold()), None)


@dataclass(slots=True)
class _Converters:
    """Lazily-built Docling converters.

    Two instances: one with OCR disabled (the fast path) and one with it enabled.
    Built lazily and cached because construction loads layout models, which costs
    seconds and should happen once per process rather than per document.
    """

    structural: Any = None
    ocr: Any = None


_converters = _Converters()


def _build_converter(*, with_ocr: bool) -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import (
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
    )

    options = PdfPipelineOptions()
    options.do_ocr = with_ocr
    options.do_table_structure = True
    # Enrichment models (code, formula, chart, picture description) are for
    # scientific and technical documents. An invoice has none of that, and each
    # one adds model load time and latency. Left off deliberately.
    options.do_code_enrichment = False
    options.do_formula_enrichment = False

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
        }
    )


def get_converter(*, with_ocr: bool) -> Any:
    if with_ocr:
        if _converters.ocr is None:
            _converters.ocr = _build_converter(with_ocr=True)
        return _converters.ocr
    if _converters.structural is None:
        _converters.structural = _build_converter(with_ocr=False)
    return _converters.structural


def _docling_version() -> str:
    try:
        import docling

        return str(getattr(docling, "__version__", "unknown"))
    except Exception:  # pragma: no cover - docling is a hard dependency
        return "unknown"


def _normalise_bbox(bbox: Any, page_width: float, page_height: float) -> BoundingBox | None:
    """Convert a Docling bbox to a normalised top-left box.

    Docling reports `CoordOrigin.BOTTOMLEFT` for PDFs, where `t` and `b` are
    measured upward from the page bottom (so `t > b`). Our `BoundingBox` is
    normalised 0..1 from the top-left, because that is what a browser overlay
    needs. Getting this inversion wrong would put every highlight on the wrong
    part of the page — visibly wrong, but only if someone looks, so it is
    covered by a test.
    """
    if bbox is None or page_width <= 0 or page_height <= 0:
        return None

    try:
        left = float(bbox.l) / page_width
        right = float(bbox.r) / page_width
        raw_top = float(bbox.t)
        raw_bottom = float(bbox.b)
    except (AttributeError, TypeError, ValueError):
        return None

    origin = getattr(getattr(bbox, "coord_origin", None), "value", "")
    if str(origin).upper() == "BOTTOMLEFT":
        top = 1.0 - (raw_top / page_height)
        bottom = 1.0 - (raw_bottom / page_height)
    else:
        top = raw_top / page_height
        bottom = raw_bottom / page_height

    def clamp(value: float) -> float:
        return min(1.0, max(0.0, value))

    left, right = clamp(left), clamp(right)
    top, bottom = clamp(top), clamp(bottom)

    # A degenerate or inverted box is dropped rather than coerced: a wrong
    # highlight is more misleading than no highlight.
    if right < left or bottom < top:
        return None

    try:
        return BoundingBox(left=left, top=top, right=right, bottom=bottom)
    except ValueError:
        return None


def _page_size(doc: Any, page_no: int) -> tuple[float, float]:
    page = doc.pages.get(page_no) if hasattr(doc, "pages") else None
    size = getattr(page, "size", None)
    if size is None:
        # US Letter at 72 dpi. Only reached if Docling omits page geometry.
        return 612.0, 792.0
    return float(size.width), float(size.height)


def _extract_regions_and_tables(
    doc: Any,
) -> tuple[tuple[ParsedRegion, ...], tuple[ParsedTable, ...]]:
    regions: list[ParsedRegion] = []
    tables: list[ParsedTable] = []

    for order, (item, _level) in enumerate(doc.iterate_items()):
        provenance = getattr(item, "prov", None) or []
        if provenance:
            page_no = int(provenance[0].page_no)
            width, height = _page_size(doc, page_no)
            bbox = _normalise_bbox(provenance[0].bbox, width, height)
        else:
            # Some items carry no provenance (synthetic groupings). Recorded at
            # page 1 without a box so the reading order stays complete.
            page_no, bbox = 1, None

        label = str(getattr(getattr(item, "label", None), "value", getattr(item, "label", "")))
        kind = _LABEL_TO_REGION_KIND.get(label.lower(), RegionKind.UNKNOWN)
        element_ref = str(getattr(item, "self_ref", f"#/items/{order}"))

        regions.append(
            ParsedRegion(
                element_ref=element_ref,
                kind=kind,
                page=page_no,
                bbox=bbox,
                text=str(getattr(item, "text", "") or ""),
                reading_order=order,
            )
        )

        if type(item).__name__ == "TableItem":
            tables.append(_parse_table(item, doc, element_ref, page_no, bbox))

    return tuple(regions), tuple(tables)


def _parse_table(
    item: Any, doc: Any, element_ref: str, page_no: int, bbox: BoundingBox | None
) -> ParsedTable:
    data = getattr(item, "data", None)
    num_rows = int(getattr(data, "num_rows", 0) or 0)
    num_cols = int(getattr(data, "num_cols", 0) or 0)

    header: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()

    try:
        # `doc` is passed explicitly: calling without it is deprecated in Docling.
        frame = item.export_to_dataframe(doc=doc)
        header = tuple(str(c) for c in frame.columns)
        rows = tuple(tuple("" if v is None else str(v) for v in row) for row in frame.values)
    except Exception as exc:
        # A table that will not export is a degraded parse, not a fatal one: the
        # markdown still contains the text and extraction can proceed. Logged so
        # it is visible rather than silent.
        logger.warning(
            "Table %s on page %s could not be exported as a grid: %s",
            element_ref,
            page_no,
            exc,
        )

    return ParsedTable(
        element_ref=element_ref,
        page=page_no,
        bbox=bbox,
        num_rows=num_rows,
        num_cols=num_cols,
        header=header,
        rows=rows,
    )


def _convert(source: Path, *, with_ocr: bool, stage: str) -> Any:
    converter = get_converter(with_ocr=with_ocr)
    try:
        result = converter.convert(source)
    except Exception as exc:
        raise ParsingError(
            f"Docling failed to convert {source.name!r} "
            f"({'OCR' if with_ocr else 'structural'} pass).",
            context=ErrorContext(stage=stage, inputs={"filename": source.name}),
            cause=exc,
        ) from exc

    status = str(getattr(getattr(result, "status", None), "name", "")).upper()
    if status not in {"SUCCESS", "PARTIAL_SUCCESS", ""}:
        raise ParsingError(
            f"Docling reported status {status} for {source.name!r}. "
            "Refusing to continue with an unreliable parse.",
            context=ErrorContext(
                stage=stage, inputs={"filename": source.name, "status": status}
            ),
        )
    return result.document


def parse_path(
    source: Path,
    *,
    media_type: str,
    document_id: str | None = None,
) -> ParsedDocument:
    """Parse a document from a local path.

    Escalates to OCR only when the structural pass yields implausibly little text.
    If OCR also yields nothing, that is a hard failure: an empty parse reported as
    success is the outcome most likely to cause a wrong decision downstream, so it
    must never be returned.
    """
    started = time.perf_counter()
    is_image = media_type.startswith("image/")

    # An image has no text layer by construction, so the structural pass would be
    # a guaranteed waste.
    if is_image:
        doc = _convert(source, with_ocr=True, stage="parse.ocr")
        strategy, escalated = ParseStrategy.OCR, False
    else:
        doc = _convert(source, with_ocr=False, stage="parse.structural")
        markdown = doc.export_to_markdown()
        if len(markdown.strip()) >= MIN_PLAUSIBLE_TEXT_CHARS:
            strategy, escalated = ParseStrategy.STRUCTURAL, False
        else:
            logger.info(
                "Structural pass on %s yielded %d chars (< %d); escalating to OCR.",
                source.name,
                len(markdown.strip()),
                MIN_PLAUSIBLE_TEXT_CHARS,
            )
            doc = _convert(source, with_ocr=True, stage="parse.ocr")
            strategy, escalated = ParseStrategy.OCR, True

    markdown = doc.export_to_markdown()
    regions, tables = _extract_regions_and_tables(doc)
    page_count = len(getattr(doc, "pages", {}) or {})

    if not markdown.strip() and not regions:
        raise ParsingError(
            f"{source.name!r} produced no text and no layout regions after "
            f"{'OCR' if strategy is ParseStrategy.OCR else 'structural'} parsing. "
            "An empty parse is not a valid result — it would look like a document "
            "with no content rather than a document we failed to read.",
            context=ErrorContext(
                stage="parse.verify",
                document_id=document_id,
                inputs={"filename": source.name, "strategy": strategy.value},
            ),
        )

    return ParsedDocument(
        document_id=document_id,
        page_count=page_count,
        strategy=strategy,
        parser_version=_docling_version(),
        markdown=markdown,
        regions=regions,
        tables=tables,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        escalated_to_ocr=escalated,
    )


def parse_payload(payload: DocumentPayload, *, document_id: str | None = None) -> ParsedDocument:
    """Parse in-memory bytes.

    Docling reads from a path, so bytes are written to a temporary file. Used by
    tests and by any store that cannot offer a local path.
    """
    from ap_agent.ingest.storage import extension_for

    with TemporaryDirectory(prefix="ap-parse-") as tmp:
        target = Path(tmp) / f"document{extension_for(payload.media_type)}"
        target.write_bytes(payload.content)
        return parse_path(target, media_type=payload.media_type, document_id=document_id)


def render_page_images(
    source: Path,
    *,
    media_type: str,
    max_pages: int = 4,
    scale: float = 2.0,
) -> list[tuple[str, bytes]]:
    """Rasterise pages to PNG for multimodal extraction (FR-2.3).

    Needed because layout parsing is lossy in a way that matters. Observed on this
    project's own fixtures: Docling absorbed the totals block into the line table and
    **discarded the "Subtotal"/"Tax"/"Total" labels entirely**, leaving three
    unlabelled amounts. The model then had to infer which was which and dropped the
    tax line — which would make `math_integrity` fail a perfectly valid invoice.

    The page image still shows those labels plainly, so attaching it recovers
    information the text pipeline destroyed. This is the concrete reason the design
    sends *both* parsed structure and source images rather than either alone.

    Uses `pypdfium2`, already present as a Docling dependency: a self-contained wheel
    with no poppler or system-library requirement, so it does not compromise
    clone-and-run.
    """
    if media_type.startswith("image/"):
        # Already an image; hand back the original bytes rather than re-encoding.
        return [(media_type, source.read_bytes())]

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - shipped with docling
        raise ParsingError(
            "pypdfium2 is required to rasterise PDF pages for multimodal extraction.",
            context=ErrorContext(stage="parse.rasterise"),
            cause=exc,
        ) from exc

    images: list[tuple[str, bytes]] = []
    pdf = None
    try:
        pdf = pdfium.PdfDocument(str(source))
        for index in range(min(len(pdf), max_pages)):
            page = pdf[index]
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            buffer = io.BytesIO()
            # Greyscale: invoices are monochrome documents, and dropping colour cuts
            # the encoded size substantially with no loss of legibility.
            pil_image.convert("L").save(buffer, format="PNG", optimize=True)
            images.append(("image/png", buffer.getvalue()))
    except Exception as exc:
        raise ParsingError(
            f"Failed to rasterise {source.name!r} for multimodal extraction.",
            context=ErrorContext(stage="parse.rasterise", inputs={"file": source.name}),
            cause=exc,
        ) from exc
    finally:
        if pdf is not None:
            pdf.close()

    return images


def regions_to_json(regions: tuple[ParsedRegion, ...]) -> list[dict[str, Any]]:
    """Serialise regions for the `documents.regions` JSONB column.

    Persisted so the UI can highlight source locations on a completed run without
    re-parsing the document — a re-parse would cost seconds and could, on a
    degraded scan, produce different boxes than the run being reviewed.
    """
    return [r.model_dump(mode="json") for r in regions]
