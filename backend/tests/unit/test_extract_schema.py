"""`RawInvoiceExtraction` and `FieldReading`: the model-facing schema.

Unit tier: no network, no Docling — these types are validated purely against
constructed payloads.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ap_agent.extract.schema import FieldReading, LineReading, RawInvoiceExtraction


def _reading(value: str = "INV-1", label: str | None = None, confidence: float = 1.0) -> dict:
    return {"value": value, "printed_label": label, "confidence": confidence}


def _minimal_payload(**overrides: object) -> dict:
    payload: dict = {
        "invoice_number": _reading("INV-60000"),
        "invoice_date": _reading("2026-02-27"),
        "vendor_name": _reading("Acme Corporation"),
        "currency": _reading("USD"),
        "subtotal": _reading("240.00"),
        "total_amount": _reading("259.80"),
    }
    payload.update(overrides)
    return payload


class TestFieldReading:
    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            FieldReading(value="x", confidence=1.0, made_up_field="nope")  # type: ignore[call-arg]

    def test_is_present_true_for_nonblank_value(self) -> None:
        assert FieldReading(value="INV-1", confidence=1.0).is_present is True

    def test_is_present_false_for_blank_value(self) -> None:
        assert FieldReading(value="   ", confidence=1.0).is_present is False

    def test_confidence_must_be_in_unit_range(self) -> None:
        with pytest.raises(ValidationError):
            FieldReading(value="x", confidence=1.5)
        with pytest.raises(ValidationError):
            FieldReading(value="x", confidence=-0.1)


class TestRawInvoiceExtractionRequiredFields:
    def test_accepts_a_minimal_valid_payload(self) -> None:
        raw = RawInvoiceExtraction.model_validate(_minimal_payload())
        assert raw.invoice_number.value == "INV-60000"
        assert raw.tax_amount is None
        assert list(raw.lines) == []

    def test_rejects_unknown_top_level_fields(self) -> None:
        with pytest.raises(ValidationError):
            RawInvoiceExtraction.model_validate(_minimal_payload(unexpected_field="x"))

    def test_missing_required_header_field_is_rejected(self) -> None:
        payload = _minimal_payload()
        del payload["currency"]
        with pytest.raises(ValidationError):
            RawInvoiceExtraction.model_validate(payload)


class TestNamelessLineDropping:
    """Docling absorbs the totals block into the line table; the model
    sometimes echoes those rows back as line items. Rejecting them is
    structural — a row with no description is not a line item at all."""

    def test_named_lines_are_kept(self) -> None:
        payload = _minimal_payload(
            lines=[
                {
                    "line_number": 1,
                    "description": "Steel bracket, 40mm",
                    "quantity": "20",
                    "unit_price": "12.00",
                    "line_total": "240.00",
                    "confidence": 1.0,
                }
            ]
        )
        raw = RawInvoiceExtraction.model_validate(payload)
        assert len(raw.lines) == 1
        assert raw.dropped_nameless_lines == 0

    def test_nameless_rows_are_dropped_and_counted(self) -> None:
        payload = _minimal_payload(
            lines=[
                {
                    "line_number": 1,
                    "description": "Steel bracket, 40mm",
                    "quantity": "20",
                    "unit_price": "12.00",
                    "line_total": "240.00",
                    "confidence": 1.0,
                },
                # A totals-block row echoed back with no description: this is
                # what Docling produces when it absorbs "Subtotal"/"Tax"/"Total"
                # into the line table and loses their labels.
                {
                    "line_number": 2,
                    "description": "",
                    "quantity": "",
                    "unit_price": "",
                    "line_total": "259.80",
                    "confidence": 0.8,
                },
            ]
        )
        raw = RawInvoiceExtraction.model_validate(payload)
        assert len(raw.lines) == 1
        assert raw.dropped_nameless_lines == 1
        assert raw.lines[0].description == "Steel bracket, 40mm"

    def test_whitespace_only_description_counts_as_nameless(self) -> None:
        payload = _minimal_payload(
            lines=[
                {
                    "line_number": 1,
                    "description": "   ",
                    "quantity": "1",
                    "unit_price": "1.00",
                    "line_total": "1.00",
                    "confidence": 1.0,
                }
            ]
        )
        raw = RawInvoiceExtraction.model_validate(payload)
        assert list(raw.lines) == []
        assert raw.dropped_nameless_lines == 1

    def test_a_named_line_with_a_bad_quantity_is_kept_not_dropped(self) -> None:
        """A genuine data defect (unreadable quantity) must survive to fail
        loudly downstream — it must not be silently discarded alongside
        genuinely nameless totals rows."""
        payload = _minimal_payload(
            lines=[
                {
                    "line_number": 1,
                    "description": "Steel bracket, 40mm",
                    "quantity": "not a number",
                    "unit_price": "12.00",
                    "line_total": "240.00",
                    "confidence": 0.5,
                }
            ]
        )
        raw = RawInvoiceExtraction.model_validate(payload)
        assert len(raw.lines) == 1
        assert raw.dropped_nameless_lines == 0
        assert raw.lines[0].quantity == "not a number"

    def test_non_list_lines_field_is_left_for_normal_validation(self) -> None:
        # Malformed input (lines is not a list at all) should surface as a
        # normal schema-validation error, not be swallowed by the pre-filter.
        with pytest.raises(ValidationError):
            RawInvoiceExtraction.model_validate(_minimal_payload(lines="not-a-list"))


class TestHeaderReadingsAndLabels:
    def test_header_readings_excludes_absent_optional_fields(self) -> None:
        raw = RawInvoiceExtraction.model_validate(_minimal_payload())
        readings = raw.header_readings()
        assert "invoice_number" in readings
        assert "tax_amount" not in readings  # never set
        assert "due_date" not in readings  # never set

    def test_header_readings_excludes_blank_optional_fields(self) -> None:
        raw = RawInvoiceExtraction.model_validate(
            _minimal_payload(due_date=_reading(""))
        )
        assert "due_date" not in raw.header_readings()

    def test_header_readings_includes_present_optional_fields(self) -> None:
        raw = RawInvoiceExtraction.model_validate(
            _minimal_payload(tax_amount=_reading("19.80", label="Tax:"))
        )
        readings = raw.header_readings()
        assert readings["tax_amount"].value == "19.80"
        assert readings["tax_amount"].printed_label == "Tax:"


class TestLineReading:
    def test_line_number_must_be_at_least_one(self) -> None:
        with pytest.raises(ValidationError):
            LineReading(
                line_number=0,
                description="x",
                quantity="1",
                unit_price="1.00",
                line_total="1.00",
                confidence=1.0,
            )
