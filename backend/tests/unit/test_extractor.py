"""`InvoiceExtractor`: parsed document -> canonical `Invoice`.

Uses `ScriptedModelClient` throughout so extraction logic — conversion,
no-coercion failure, label resolution, image attachment policy — is exercised
without network access or AWS credentials. Live-model behaviour is covered
separately by `scripts/extract_demo.py` (manual) and the `bedrock`-marked
integration tier.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ap_agent.core.primitives import BoundingBox, ExtractionMethod, RegionKind
from ap_agent.errors import ExtractionError
from ap_agent.extract.bedrock import ScriptedModelClient
from ap_agent.extract.extractor import (
    InvoiceExtractor,
    render_tables,
    resolve_labels,
    should_attach_images,
)
from ap_agent.extract.schema import FieldReading, LineReading, RawInvoiceExtraction
from ap_agent.ingest.parsing import ParsedDocument, ParsedRegion, ParsedTable, ParseStrategy


def _reading(value: str, label: str | None = None, confidence: float = 1.0) -> FieldReading:
    return FieldReading(value=value, printed_label=label, confidence=confidence)


def _line(
    number: int,
    description: str = "Steel bracket, 40mm",
    quantity: str = "20",
    unit_price: str = "12.00",
    line_total: str = "240.00",
    confidence: float = 1.0,
) -> LineReading:
    return LineReading(
        line_number=number,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        line_total=line_total,
        confidence=confidence,
    )


def _region(
    order: int, text: str, *, left: float = 0.1, top: float = 0.1, right: float = 0.5,
    bottom: float = 0.11,
) -> ParsedRegion:
    return ParsedRegion(
        element_ref=f"#/texts/{order}",
        kind=RegionKind.TEXT,
        page=1,
        bbox=BoundingBox(left=left, top=top, right=right, bottom=bottom),
        text=text,
        reading_order=order,
    )


def _parsed(
    *,
    regions: list[ParsedRegion] | None = None,
    tables: tuple[ParsedTable, ...] = (),
    strategy: ParseStrategy = ParseStrategy.STRUCTURAL,
    escalated: bool = False,
    picture_count: int = 0,
) -> ParsedDocument:
    regions = list(regions or [])
    for i in range(picture_count):
        regions.append(
            ParsedRegion(
                element_ref=f"#/pictures/{i}",
                kind=RegionKind.PICTURE,
                page=1,
                bbox=None,
                text="",
                reading_order=1000 + i,
            )
        )
    markdown = "\n".join(r.text for r in regions if r.text)
    return ParsedDocument(
        page_count=1,
        strategy=strategy,
        parser_version="test",
        markdown=markdown,
        regions=tuple(regions),
        tables=tables,
        duration_ms=1.0,
        escalated_to_ocr=escalated,
    )


def _minimal_raw(**overrides: object) -> RawInvoiceExtraction:
    payload: dict = {
        "invoice_number": _reading("INV-60000", "Invoice Number:"),
        "invoice_date": _reading("2026-02-27", "Invoice Date:"),
        "vendor_name": _reading("Acme Corporation"),
        "currency": _reading("USD"),
        "subtotal": _reading("240.00", "Subtotal:"),
        "total_amount": _reading("259.80", "Total:"),
    }
    payload.update(overrides)
    return RawInvoiceExtraction.model_validate(payload)


class TestShouldAttachImages:
    def test_ocr_strategy_attaches_images(self) -> None:
        assert should_attach_images(_parsed(strategy=ParseStrategy.OCR)) is True

    def test_pictures_present_attaches_images(self) -> None:
        assert should_attach_images(_parsed(picture_count=1)) is True

    def test_clean_structural_parse_does_not_attach(self) -> None:
        assert should_attach_images(_parsed()) is False


class TestRenderTables:
    def test_empty_when_no_tables(self) -> None:
        assert render_tables(_parsed()) == ""

    def test_preserves_column_structure(self) -> None:
        table = ParsedTable(
            element_ref="#/tables/0",
            page=1,
            bbox=None,
            num_rows=1,
            num_cols=4,
            header=("Description", "Qty", "Unit Price", "Amount"),
            rows=(("Steel bracket, 40mm", "20 EA", "USD 12.00", "USD 240.00"),),
        )
        rendered = render_tables(_parsed(tables=(table,)))
        assert "Description | Qty | Unit Price | Amount" in rendered
        assert "Steel bracket, 40mm | 20 EA | USD 12.00 | USD 240.00" in rendered

    def test_skips_empty_tables(self) -> None:
        empty_table = ParsedTable(
            element_ref="#/tables/0", page=1, bbox=None, num_rows=0, num_cols=0
        )
        assert render_tables(_parsed(tables=(empty_table,))) == ""


class TestExtractHappyPath:
    def test_converts_a_complete_reading_to_canonical(self) -> None:
        raw = _minimal_raw(lines=[_line(1)])
        client = ScriptedModelClient([raw])
        parsed = _parsed(
            regions=[
                _region(0, "Invoice Number: INV-60000"),
                _region(1, "Acme Corporation"),
            ]
        )

        outcome = InvoiceExtractor(client).extract(
            parsed, tenant_id="t1", document_id="doc-1", invoice_id="inv-1"
        )

        inv = outcome.invoice
        assert inv.invoice_number.value == "INV-60000"
        assert inv.subtotal.value.amount == Decimal("240.00")
        assert inv.total_amount.value.amount == Decimal("259.80")
        assert inv.invoice_date.value == date(2026, 2, 27)
        assert len(inv.lines) == 1
        assert inv.lines[0].line_total.value.amount == Decimal("240.00")
        assert outcome.model_id == "unknown"  # ScriptedModelClient has no model_id
        assert outcome.attempts_used == 1

    def test_non_po_invoice_has_no_po_reference(self) -> None:
        raw = _minimal_raw()
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        assert outcome.invoice.po_reference is None
        assert outcome.invoice.is_non_po is True

    def test_po_reference_present_when_readable(self) -> None:
        raw = _minimal_raw(po_reference=_reading("PO-2001", "PO Number:"))
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        assert outcome.invoice.is_non_po is False
        assert outcome.invoice.po_reference is not None
        assert outcome.invoice.po_reference.value == "PO-2001"

    def test_records_token_usage_and_image_count(self) -> None:
        raw = _minimal_raw()
        client = ScriptedModelClient([raw], usage={"input_tokens": 500, "output_tokens": 80})
        outcome = InvoiceExtractor(client).extract(
            _parsed(picture_count=1),
            tenant_id="t1",
            document_id="d1",
            invoice_id="i1",
            page_images=[("image/png", b"fake-bytes")],
        )
        assert outcome.input_tokens == 500
        assert outcome.output_tokens == 80
        assert outcome.images_attached == 1


class TestImageAttachmentPolicy:
    def test_images_not_forwarded_when_parse_does_not_warrant_them(self) -> None:
        client = ScriptedModelClient([_minimal_raw()])
        InvoiceExtractor(client).extract(
            _parsed(),  # clean structural parse, no pictures
            tenant_id="t1",
            document_id="d1",
            invoice_id="i1",
            page_images=[("image/png", b"fake-bytes")],
        )
        assert client.calls[0]["image_count"] == 0

    def test_images_forwarded_when_ocr_was_used(self) -> None:
        client = ScriptedModelClient([_minimal_raw()])
        InvoiceExtractor(client).extract(
            _parsed(strategy=ParseStrategy.OCR),
            tenant_id="t1",
            document_id="d1",
            invoice_id="i1",
            page_images=[("image/png", b"fake-bytes")],
        )
        assert client.calls[0]["image_count"] == 1

    def test_image_count_capped_at_max_attached(self) -> None:
        client = ScriptedModelClient([_minimal_raw()])
        many_images = [("image/png", b"x") for _ in range(10)]
        InvoiceExtractor(client).extract(
            _parsed(strategy=ParseStrategy.OCR),
            tenant_id="t1",
            document_id="d1",
            invoice_id="i1",
            page_images=many_images,
        )
        assert client.calls[0]["image_count"] == 4  # MAX_ATTACHED_IMAGES


class TestQuantityUnitSeparation:
    """A Qty cell often prints its unit beside the number ("10 EA"). The number
    is the quantity; the unit belongs in unit_of_measure. Splitting the two is
    recovery of what is printed, not coercion — INC-019, prompt v1.3.2."""

    def test_unit_suffixed_quantity_is_split(self) -> None:
        raw = _minimal_raw(lines=[_line(1, quantity="20 EA", unit_price="12.00")])
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        line = outcome.invoice.lines[0]
        assert line.quantity.value == Decimal("20")
        assert line.unit_of_measure is not None
        assert line.unit_of_measure.value == "EA"

    def test_decimal_quantity_with_unit_is_split(self) -> None:
        raw = _minimal_raw(lines=[_line(1, quantity="2.5 KG")])
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        line = outcome.invoice.lines[0]
        assert line.quantity.value == Decimal("2.5")
        assert line.unit_of_measure is not None
        assert line.unit_of_measure.value == "KG"

    def test_model_supplied_unit_takes_precedence_over_split(self) -> None:
        raw = _minimal_raw(
            lines=[
                LineReading(
                    line_number=1,
                    description="Steel bracket, 40mm",
                    quantity="20 EA",
                    unit_price="12.00",
                    line_total="240.00",
                    unit_of_measure="BOX",
                    confidence=1.0,
                )
            ]
        )
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        line = outcome.invoice.lines[0]
        assert line.quantity.value == Decimal("20")
        assert line.unit_of_measure is not None
        assert line.unit_of_measure.value == "BOX"

    def test_bare_quantity_keeps_null_unit(self) -> None:
        raw = _minimal_raw(lines=[_line(1, quantity="20")])
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        line = outcome.invoice.lines[0]
        assert line.quantity.value == Decimal("20")
        assert line.unit_of_measure is None

    def test_genuinely_unreadable_quantity_still_fails_loudly(self) -> None:
        # Not a "<number> <unit>" shape, so the split leaves it untouched and the
        # no-coercion parser rejects it — the fail-loud contract is preserved.
        raw = _minimal_raw(lines=[_line(1, quantity="N/A")])
        client = ScriptedModelClient([raw])
        with pytest.raises(ExtractionError, match="quantity"):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )


class TestNoCoercionOnFailure:
    """FR-2.4: a value that will not parse into its canonical type must fail
    loudly, never be defaulted, dropped, or best-guessed."""

    def test_unreadable_required_text_field_raises(self) -> None:
        raw = _minimal_raw(invoice_number=_reading(""))
        client = ScriptedModelClient([raw])
        with pytest.raises(ExtractionError, match="invoice_number"):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )

    def test_unreadable_currency_raises(self) -> None:
        raw = _minimal_raw(currency=_reading(""))
        client = ScriptedModelClient([raw])
        with pytest.raises(ExtractionError, match="Currency"):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )

    def test_non_iso_date_raises_rather_than_reinterpreting(self) -> None:
        raw = _minimal_raw(invoice_date=_reading("27/02/2026"))
        client = ScriptedModelClient([raw])
        with pytest.raises(ExtractionError, match="ISO date"):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )

    def test_non_decimal_amount_raises(self) -> None:
        raw = _minimal_raw(subtotal=_reading("not-a-number"))
        client = ScriptedModelClient([raw])
        with pytest.raises(ExtractionError, match="decimal"):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )

    def test_required_amount_absent_raises(self) -> None:
        raw = _minimal_raw(total_amount=_reading(""))
        client = ScriptedModelClient([raw])
        with pytest.raises(ExtractionError, match="total_amount"):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )

    def test_model_exception_is_wrapped_as_extraction_error(self) -> None:
        client = ScriptedModelClient(raises=RuntimeError("boom"))
        with pytest.raises(ExtractionError):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )

    def test_none_result_is_rejected(self) -> None:
        client = ScriptedModelClient([None])
        with pytest.raises(ExtractionError, match="no parsed extraction"):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )

    def test_wrong_type_result_is_rejected(self) -> None:
        client = ScriptedModelClient(["not-a-raw-extraction"])
        with pytest.raises(ExtractionError, match="Expected RawInvoiceExtraction"):
            InvoiceExtractor(client).extract(
                _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
            )


class TestRemitToConversion:
    def test_last4_is_kept_even_if_model_returns_more_digits(self) -> None:
        # Defence in depth (FR-15.7): even though the prompt forbids a full
        # account number, the harness enforces the last-four-only rule itself.
        raw = _minimal_raw(remit_to_account_last4=_reading("123456784821"))
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        assert outcome.invoice.remit_to is not None
        assert outcome.invoice.remit_to.value.account_number_last4 == "4821"

    def test_absent_remit_to_is_none(self) -> None:
        raw = _minimal_raw()
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        assert outcome.invoice.remit_to is None

    def test_bank_name_alone_is_sufficient(self) -> None:
        raw = _minimal_raw(remit_to_bank_name=_reading("First National"))
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        assert outcome.invoice.remit_to is not None
        assert outcome.invoice.remit_to.value.bank_name == "First National"
        assert outcome.invoice.remit_to.value.account_number_last4 is None


class TestExtractionMethodTagging:
    def test_structural_parse_tags_parsed_structure_method(self) -> None:
        raw = _minimal_raw()
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(strategy=ParseStrategy.STRUCTURAL),
            tenant_id="t1",
            document_id="d1",
            invoice_id="i1",
        )
        assert outcome.invoice.invoice_number.method is ExtractionMethod.PARSED_STRUCTURE

    def test_ocr_parse_tags_ocr_method(self) -> None:
        raw = _minimal_raw()
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(strategy=ParseStrategy.OCR),
            tenant_id="t1",
            document_id="d1",
            invoice_id="i1",
        )
        assert outcome.invoice.invoice_number.method is ExtractionMethod.OCR


class TestLabelResolutionIntegration:
    def test_structural_label_is_attached_to_the_canonical_field(self) -> None:
        raw = _minimal_raw()
        client = ScriptedModelClient([raw])
        parsed = _parsed(
            regions=[
                _region(0, "Invoice Number:", left=0.625, top=0.103, right=0.732, bottom=0.113),
                _region(1, "INV-60000", left=0.841, top=0.103, right=0.912, bottom=0.113),
            ]
        )
        outcome = InvoiceExtractor(client).extract(
            parsed, tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        assert outcome.invoice.invoice_number.source_label == "Invoice Number:"
        assert outcome.observed_labels["invoice_number"].text == "Invoice Number:"

    def test_model_reported_label_used_when_structure_has_nothing(self) -> None:
        raw = _minimal_raw(subtotal=_reading("240.00", "Subtotal:"))
        client = ScriptedModelClient([raw])
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        assert outcome.invoice.subtotal.source_label == "Subtotal:"
        assert outcome.observed_labels["subtotal"].source.value == "model_reported"

    def test_resolve_labels_only_covers_present_header_fields(self) -> None:
        raw = _minimal_raw()
        labels = resolve_labels(raw, _parsed())
        assert "tax_amount" not in labels  # never present on this raw payload
