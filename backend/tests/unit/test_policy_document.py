"""`completeness` (FR-4.4)."""

from __future__ import annotations

from ap_agent.core.checks import Verdict
from ap_agent.policy.document import CHECK_NAME, completeness
from tests.unit.policy_fixtures import extracted, invoice, invoice_line


class TestCompleteness:
    def test_a_fully_populated_invoice_passes(self) -> None:
        result = completeness(invoice())
        assert result.verdict is Verdict.PASS
        assert result.name == CHECK_NAME
        assert result.forces_review is False

    def test_blank_vendor_name_fails(self) -> None:
        inv = invoice().model_copy(update={"vendor_name": extracted("   ")})
        result = completeness(inv)
        assert result.verdict is Verdict.FAIL
        assert result.forces_review is True
        assert "vendor_name" in result.inputs["missing_header_fields"]

    def test_blank_invoice_number_fails(self) -> None:
        inv = invoice().model_copy(update={"invoice_number": extracted("")})
        result = completeness(inv)
        assert result.verdict is Verdict.FAIL
        assert "invoice_number" in result.inputs["missing_header_fields"]

    def test_line_with_no_description_fails(self) -> None:
        blank_line = invoice_line(description="   ")
        inv = invoice(lines=(blank_line,))
        result = completeness(inv)
        assert result.verdict is Verdict.FAIL
        assert result.inputs["lineless_line_numbers"] == [1]

    def test_multiple_defects_are_all_reported(self) -> None:
        inv = invoice(lines=(invoice_line(description=""),)).model_copy(
            update={"vendor_name": extracted("")}
        )
        result = completeness(inv)
        assert result.verdict is Verdict.FAIL
        assert result.inputs["missing_header_fields"] == ["vendor_name"]
        assert result.inputs["lineless_line_numbers"] == [1]

    def test_header_only_invoice_with_no_lines_is_not_penalised_for_lines(self) -> None:
        inv = invoice(lines=())
        result = completeness(inv)
        assert result.verdict is Verdict.PASS
