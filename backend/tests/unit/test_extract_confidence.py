"""Objective extraction confidence (`extract.confidence`).

The load-bearing property under test: model self-report alone is not a usable
signal (it was measured at a flat 1.00 across every fixture format, see
INC-004), so `derive_confidence` must actually move the score down in response
to real, measurable degradation — OCR parsing, repair attempts, arithmetic
mismatch, and unattributed labels — and must never move it up.
"""

from __future__ import annotations

from ap_agent.extract.bedrock import ScriptedModelClient
from ap_agent.extract.confidence import derive_confidence
from ap_agent.extract.extractor import InvoiceExtractor
from ap_agent.extract.schema import FieldReading, LineReading, RawInvoiceExtraction
from ap_agent.ingest.parsing import ParsedDocument, ParseStrategy


def _reading(value: str, label: str | None = None, confidence: float = 1.0) -> FieldReading:
    return FieldReading(value=value, printed_label=label, confidence=confidence)


def _line(
    number: int = 1,
    quantity: str = "20",
    unit_price: str = "12.00",
    line_total: str = "240.00",
) -> LineReading:
    return LineReading(
        line_number=number,
        description="Steel bracket, 40mm",
        quantity=quantity,
        unit_price=unit_price,
        line_total=line_total,
        confidence=1.0,
    )


def _fully_labelled_raw(**overrides: object) -> RawInvoiceExtraction:
    """A reading where every aliasable field carries a printed label and
    totals reconcile exactly — the case with no confidence factors at all."""
    payload: dict = {
        "invoice_number": _reading("INV-60000", "Invoice Number:"),
        "invoice_date": _reading("2026-02-27", "Invoice Date:"),
        "vendor_name": _reading("Acme Corporation"),
        "currency": _reading("USD"),
        "subtotal": _reading("240.00", "Subtotal:"),
        "tax_amount": _reading("19.80", "Tax:"),
        "total_amount": _reading("259.80", "Total:"),
        "due_date": _reading("2026-03-29", "Due Date:"),
        "payment_terms": _reading("Net 30", "Payment Terms:"),
        "po_reference": _reading("PO-2001", "PO Number:"),
        "lines": [_line()],
    }
    payload.update(overrides)
    return RawInvoiceExtraction.model_validate(payload)


def _parsed(*, strategy: ParseStrategy = ParseStrategy.STRUCTURAL, escalated: bool = False) -> ParsedDocument:
    return ParsedDocument(
        page_count=1,
        strategy=strategy,
        parser_version="test",
        markdown="",
        regions=(),
        tables=(),
        duration_ms=1.0,
        escalated_to_ocr=escalated,
    )


def _extract(raw: RawInvoiceExtraction, *, parsed: ParsedDocument, attempts: int = 1):
    responses = [raw] * attempts
    client = ScriptedModelClient(responses)
    return InvoiceExtractor(client).extract(
        parsed, tenant_id="t1", document_id="d1", invoice_id="i1"
    )


class TestCleanExtractionHasNoFactors:
    def test_fully_labelled_reconciling_extraction_is_unadjusted(self) -> None:
        outcome = _extract(_fully_labelled_raw(), parsed=_parsed())
        result = derive_confidence(outcome)

        assert result.factors == ()
        assert result.adjusted == result.model_reported == 1.0
        assert result.was_adjusted is False


class TestOcrPenalty:
    def test_ocr_parse_lowers_confidence(self) -> None:
        outcome = _extract(_fully_labelled_raw(), parsed=_parsed(strategy=ParseStrategy.OCR))
        result = derive_confidence(outcome)

        assert result.adjusted < result.model_reported
        assert any(f.name == "ocr_parse" for f in result.factors)

    def test_escalated_ocr_is_penalised_more_than_direct_ocr(self) -> None:
        direct = _extract(
            _fully_labelled_raw(), parsed=_parsed(strategy=ParseStrategy.OCR, escalated=False)
        )
        escalated = _extract(
            _fully_labelled_raw(), parsed=_parsed(strategy=ParseStrategy.OCR, escalated=True)
        )

        direct_score = derive_confidence(direct).adjusted
        escalated_score = derive_confidence(escalated).adjusted
        assert escalated_score < direct_score

    def test_structural_parse_has_no_ocr_penalty(self) -> None:
        outcome = _extract(_fully_labelled_raw(), parsed=_parsed(strategy=ParseStrategy.STRUCTURAL))
        result = derive_confidence(outcome)
        assert not any(f.name == "ocr_parse" for f in result.factors)


class TestRepairAttemptPenalty:
    def test_no_penalty_on_first_attempt_success(self) -> None:
        outcome = _extract(_fully_labelled_raw(), parsed=_parsed(), attempts=1)
        result = derive_confidence(outcome)
        assert not any(f.name == "repair_attempts" for f in result.factors)

    def test_two_attempts_lowers_confidence(self) -> None:
        client = ScriptedModelClient([_fully_labelled_raw()], usage={
            "input_tokens": 10, "output_tokens": 10, "total_tokens": 20,
        })
        outcome = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        # ScriptedModelClient always reports attempts_used absent -> extractor
        # defaults to 1; simulate a repaired extraction directly via the
        # ExtractionOutcome's own field instead of faking bedrock retry plumbing.
        adjusted_outcome = outcome.model_copy(update={"attempts_used": 2})
        result = derive_confidence(adjusted_outcome)

        assert any(f.name == "repair_attempts" for f in result.factors)
        assert result.adjusted < result.model_reported

    def test_more_attempts_penalise_more(self) -> None:
        client = ScriptedModelClient([_fully_labelled_raw()])
        base = InvoiceExtractor(client).extract(
            _parsed(), tenant_id="t1", document_id="d1", invoice_id="i1"
        )
        two = derive_confidence(base.model_copy(update={"attempts_used": 2})).adjusted
        three = derive_confidence(base.model_copy(update={"attempts_used": 3})).adjusted
        assert three < two


class TestArithmeticMismatchPenalty:
    def test_reconciling_totals_have_no_penalty(self) -> None:
        outcome = _extract(_fully_labelled_raw(), parsed=_parsed())
        result = derive_confidence(outcome)
        assert not any(f.name == "arithmetic_mismatch" for f in result.factors)

    def test_tax_plus_subtotal_not_equal_total_is_penalised(self) -> None:
        raw = _fully_labelled_raw(total_amount=_reading("300.00", "Total:"))
        outcome = _extract(raw, parsed=_parsed())
        result = derive_confidence(outcome)

        assert any(f.name == "arithmetic_mismatch" for f in result.factors)
        assert result.adjusted < result.model_reported

    def test_line_sum_not_matching_subtotal_is_penalised(self) -> None:
        raw = _fully_labelled_raw(
            subtotal=_reading("500.00", "Subtotal:"),
            total_amount=_reading("519.80", "Total:"),
        )
        outcome = _extract(raw, parsed=_parsed())
        result = derive_confidence(outcome)
        assert any(f.name == "arithmetic_mismatch" for f in result.factors)

    def test_no_lines_and_no_tax_is_not_penalised(self) -> None:
        # Nothing to check -> absence of evidence must not be scored as a
        # mismatch.
        raw = _fully_labelled_raw(tax_amount=None, lines=[], total_amount=_reading("240.00", "Total:"))
        outcome = _extract(raw, parsed=_parsed())
        result = derive_confidence(outcome)
        assert not any(f.name == "arithmetic_mismatch" for f in result.factors)

    def test_tiny_rounding_drift_is_tolerated(self) -> None:
        raw = _fully_labelled_raw(total_amount=_reading("259.81", "Total:"))  # 1 cent off
        outcome = _extract(raw, parsed=_parsed())
        result = derive_confidence(outcome)
        assert not any(f.name == "arithmetic_mismatch" for f in result.factors)


class TestUnattributedLabelPenalty:
    def test_all_labelled_has_no_penalty(self) -> None:
        outcome = _extract(_fully_labelled_raw(), parsed=_parsed())
        result = derive_confidence(outcome)
        assert not any(f.name == "unattributed_labels" for f in result.factors)

    def test_missing_label_on_an_aliasable_field_is_penalised(self) -> None:
        raw = _fully_labelled_raw(subtotal=_reading("240.00", None))
        outcome = _extract(raw, parsed=_parsed())
        result = derive_confidence(outcome)

        factor = next(f for f in result.factors if f.name == "unattributed_labels")
        assert "subtotal" in factor.reasoning

    def test_more_unlabelled_fields_penalise_more(self) -> None:
        one_missing = _extract(
            _fully_labelled_raw(subtotal=_reading("240.00", None)), parsed=_parsed()
        )
        two_missing = _extract(
            _fully_labelled_raw(
                subtotal=_reading("240.00", None), tax_amount=_reading("19.80", None)
            ),
            parsed=_parsed(),
        )
        assert derive_confidence(two_missing).adjusted < derive_confidence(one_missing).adjusted


class TestFactorsNeverIncreaseScore:
    def test_adjusted_is_never_greater_than_model_reported(self) -> None:
        cases = [
            _fully_labelled_raw(),
            _fully_labelled_raw(subtotal=_reading("240.00", None)),
            _fully_labelled_raw(total_amount=_reading("999.00", "Total:")),
        ]
        for raw in cases:
            for strategy in (ParseStrategy.STRUCTURAL, ParseStrategy.OCR):
                outcome = _extract(raw, parsed=_parsed(strategy=strategy))
                result = derive_confidence(outcome)
                assert result.adjusted <= result.model_reported

    def test_adjusted_stays_within_unit_range(self) -> None:
        raw = _fully_labelled_raw(
            subtotal=_reading("500.00", None),
            tax_amount=_reading("1.00", None),
            total_amount=_reading("999.00", None),
        )
        outcome = _extract(raw, parsed=_parsed(strategy=ParseStrategy.OCR, escalated=True))
        outcome = outcome.model_copy(update={"attempts_used": 3})
        result = derive_confidence(outcome)
        assert 0.0 <= result.adjusted <= 1.0
