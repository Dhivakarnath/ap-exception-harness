"""Document-level integrity: `completeness` (FR-4.4).

The first check that should run on any invoice, and the cheapest kind of
correctness: does the document itself contain what a tax authority and an AP
clerk would both expect to see before anything else is evaluated. There is no
tolerance band or policy configuration here — a required field is either
present or it is not.
"""

from __future__ import annotations

from ap_agent.core.canonical import Invoice
from ap_agent.core.checks import CheckCategory, CheckResult, Severity, Verdict

CHECK_NAME = "completeness"

# Header fields a payable invoice must carry regardless of tenant. Chosen from
# FR-4.4: issuer (vendor name), a unique invoice number, an issue date, and —
# separately, per line — a description of what was billed. Currency and
# amounts are deliberately excluded here: their *presence* is enforced by
# extraction (`ExtractionError` on an unreadable required field, FR-2.4)
# before an `Invoice` can exist at all, so re-checking them here would be
# redundant. This check is about what a *complete* invoice states, not what a
# *parseable* one contains.
_REQUIRED_HEADER_FIELDS: tuple[str, ...] = (
    "vendor_name",
    "invoice_number",
    "invoice_date",
)


def completeness(invoice: Invoice) -> CheckResult:
    """Required header fields and line descriptions are present and non-blank.

    Blank-but-present is treated the same as absent: a `vendor_name` of
    `"   "` satisfies no tax authority's requirement any more than a missing
    one would, and `Extracted[str]` does not itself forbid whitespace-only
    values (that would be too strict a rule to live in the domain model,
    since a field can legitimately be blank-but-present in other contexts).
    """
    missing: list[str] = []

    for field_name in _REQUIRED_HEADER_FIELDS:
        extracted = getattr(invoice, field_name)
        value = extracted.value
        if isinstance(value, str) and not value.strip():
            missing.append(field_name)

    lineless_positions = [
        line.line_number for line in invoice.lines if not line.description.value.strip()
    ]

    if missing or lineless_positions:
        parts: list[str] = []
        if missing:
            parts.append(f"missing/blank header field(s): {', '.join(missing)}")
        if lineless_positions:
            positions = ", ".join(str(p) for p in lineless_positions)
            parts.append(f"line(s) with no description: {positions}")
        reasoning = f"Invoice is incomplete — {'; '.join(parts)}."
        return CheckResult(
            name=CHECK_NAME,
            category=CheckCategory.DOCUMENT,
            verdict=Verdict.FAIL,
            severity=Severity.HIGH,
            reasoning=reasoning,
            threshold=f"required: {', '.join(_REQUIRED_HEADER_FIELDS)}, all lines described",
            actual=reasoning,
            inputs={
                "missing_header_fields": missing,
                "lineless_line_numbers": lineless_positions,
            },
            forces_review=True,
        )

    return CheckResult(
        name=CHECK_NAME,
        category=CheckCategory.DOCUMENT,
        verdict=Verdict.PASS,
        reasoning=(
            "All required header fields are present and every line carries a "
            "description."
        ),
        threshold=f"required: {', '.join(_REQUIRED_HEADER_FIELDS)}, all lines described",
        actual="all present",
    )
