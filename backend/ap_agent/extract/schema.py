"""The schema the model fills in.

Deliberately **not** the canonical `Invoice`. Three reasons:

**Amounts come back as strings.** A JSON number would arrive as a float and
reintroduce the binary rounding error `Money` exists to prevent. The model reports
`"259.80"`; the mapping layer parses it to `Decimal` and fails loudly if it
cannot.

**Every reading carries its printed label.** The model reports that it found the
total under the heading "Grand Total". That label is what lets a first-time vendor
format be promoted into a deterministic alias rule once a human confirms it
(FR-3.2). Without it, every invoice from that vendor would need inference forever.

**Confidence is per field, not per document.** A confident vendor name does not
redeem a badly-read total, and confidence gates auto-approval, so it must be
attributed to the field whose value would move money.

The model is asked for a flat, shallow structure. Nesting `Extracted[T]` directly
would be more elegant on paper and materially worse in practice — deep schemas
raise malformed-output rates, and a malformed extraction here is a hard failure
rather than something we can paper over.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldReading(BaseModel):
    """One value the model read off the document.

    `value` is always a string. Typing happens in the mapping layer where a parse
    failure can be reported precisely rather than silently coerced.
    """

    model_config = ConfigDict(extra="forbid")

    value: str = Field(
        description=(
            "The value exactly as printed, with currency symbols and thousands "
            "separators removed. Dates as YYYY-MM-DD. Amounts as a plain decimal "
            "string such as 1234.56. Use an empty string if the field is absent."
        )
    )
    printed_label: str | None = Field(
        default=None,
        description=(
            "The label printed next to this value on the document, verbatim "
            "(for example 'Grand Total' or 'Your Order No'). Null if the value "
            "had no visible label."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How certain you are this value is correct and complete. Lower it when "
            "the text is blurred, ambiguous, partially cut off, or when you had to "
            "choose between candidates."
        ),
    )

    @property
    def is_present(self) -> bool:
        return bool(self.value.strip())


class LineReading(BaseModel):
    """One line item."""

    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(ge=1, description="1-based position in the line table.")
    description: str = Field(description="Item or service description as printed.")
    quantity: str = Field(
        description="Quantity as a plain decimal string. Use '1' if not stated."
    )
    unit_price: str = Field(description="Unit price as a plain decimal string.")
    line_total: str = Field(description="Line total as a plain decimal string.")
    unit_of_measure: str | None = Field(
        default=None, description="Unit such as EA, KG, HR. Null if not stated."
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Certainty for this line as a whole."
    )


class RawInvoiceExtraction(BaseModel):
    """What the model returns for one invoice.

    `extra="forbid"` so an invented field is a validation error rather than a
    silently ignored hallucination.
    """

    model_config = ConfigDict(extra="forbid")

    # --- header ---
    invoice_number: FieldReading
    invoice_date: FieldReading
    vendor_name: FieldReading
    currency: FieldReading = Field(
        description="ISO 4217 code, e.g. USD. Infer from symbols if not stated explicitly."
    )
    subtotal: FieldReading
    total_amount: FieldReading

    # --- optional header ---
    tax_amount: FieldReading | None = Field(
        default=None,
        description=(
            "TOTAL tax on the invoice. If several tax or levy rows are printed "
            "(e.g. State Tax plus City Tax, or VAT plus a duty), report their SUM "
            "here, and list the individual rows in tax_breakdown. A single 0.00 "
            "under a reverse-charge note is a valid value, not a missing one."
        ),
    )
    tax_breakdown: list[FieldReading] = Field(
        default_factory=list,
        description=(
            "One entry per printed tax or levy row, when more than one exists. "
            "Each carries the row's own printed label and amount. Leave empty for "
            "a single-tax invoice (tax_amount alone is enough). The amounts here "
            "must sum to tax_amount."
        ),
    )
    due_date: FieldReading | None = None
    payment_terms: FieldReading | None = None
    po_reference: FieldReading | None = Field(
        default=None,
        description=(
            "Purchase order number if the invoice cites one. Null when the invoice "
            "genuinely has no PO reference — do not invent one, and do not reuse an "
            "unrelated number such as a quote or delivery note."
        ),
    )

    # --- remittance (the bank-detail-change surface) ---
    remit_to_account_last4: FieldReading | None = Field(
        default=None,
        description=(
            "Last four digits only of any bank account printed for remittance. "
            "Never return a full account number."
        ),
    )
    remit_to_bank_name: FieldReading | None = None

    # --- lines ---
    lines: list[LineReading] = Field(
        default_factory=list,
        description=(
            "Line items in printed order. Empty is acceptable for a header-only "
            "service invoice. Do not include subtotal, tax, or total rows as lines."
        ),
    )
    dropped_nameless_lines: int = Field(
        default=0,
        ge=0,
        description=(
            "Set by validation, not by the model: how many nameless rows were "
            "discarded from `lines`. Non-zero means the model echoed totals rows "
            "back as line items."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_nameless_lines(cls, data: Any) -> Any:
        """Discard rows that are not line items at all.

        Layout extraction frequently loses the "Subtotal / Tax / Total" labels while
        keeping their amounts, leaving trailing table rows with empty description,
        quantity, and price cells. Despite explicit instruction, a model sometimes
        echoes those back as line items.

        A row with no description is not a line item — no real invoice line is
        nameless. Dropping it is **structural rejection**, categorically different
        from coercing a value: we are not inventing a missing quantity, we are
        declining to treat a totals row as a good or service.

        A row that *does* have a description but an unreadable quantity is kept and
        will fail loudly during conversion. That is a genuine data defect and must
        not be silently discarded.

        The count is recorded so the filtering is visible rather than silent.
        """
        if not isinstance(data, dict):
            return data

        lines = data.get("lines")
        if not isinstance(lines, list):
            return data

        def named(line: object) -> bool:
            if isinstance(line, LineReading):
                return bool(line.description.strip())
            if isinstance(line, dict):
                return bool(str(line.get("description") or "").strip())
            return True  # leave anything unexpected for field validation to reject

        kept = [line for line in lines if named(line)]
        if len(kept) != len(lines):
            data = {**data, "lines": kept, "dropped_nameless_lines": len(lines) - len(kept)}
        return data

    # --- self-assessment ---
    line_currency_differs_from_header: bool = Field(
        default=False,
        description=(
            "True if the currency printed against the line amounts differs from the "
            "header currency. Report it; do not attempt to reconcile it."
        ),
    )
    unreadable_regions: list[str] = Field(
        default_factory=list,
        description=(
            "Short notes on anything you could not read, e.g. 'tax line obscured'. "
            "Report gaps rather than guessing at them."
        ),
    )

    # ------------------------------------------------------------------ helpers
    def header_readings(self) -> dict[str, FieldReading]:
        """Present header readings, keyed by canonical field name."""
        candidates: dict[str, FieldReading | None] = {
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "vendor_name": self.vendor_name,
            "currency": self.currency,
            "subtotal": self.subtotal,
            "total_amount": self.total_amount,
            "tax_amount": self.tax_amount,
            "due_date": self.due_date,
            "payment_terms": self.payment_terms,
            "po_reference": self.po_reference,
        }
        return {k: v for k, v in candidates.items() if v is not None and v.is_present}

    # Label harvesting for alias learning (FR-3.2) deliberately does **not** live
    # here. The model's `printed_label` proved unstable across prompt revisions —
    # nulling every label in one version and half of them in the next — while the
    # same labels sit in the parse as addressable layout elements with geometry.
    # `mapping.labels.resolve_label` recovers them structurally and falls back to
    # `printed_label` only for labels the text layer cannot reach, such as a totals
    # block Docling absorbed into the line table. The field stays on the schema as
    # that fallback input; it is not the primary source.
