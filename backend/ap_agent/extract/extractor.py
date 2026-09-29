"""Extraction: parsed document -> canonical `Invoice`.

The pipeline stage where probabilistic reading meets a typed domain model, and
therefore the stage where a wrong number can enter the system unnoticed. The
governing rule is **no coercion** (FR-2.4): if a value will not parse into its
canonical type, the run fails loudly rather than substituting a default, dropping
the field, or best-guessing. A silently-defaulted total is a wrong payment
authorisation nobody sees; a failed run is a visible problem someone fixes.

What happens here:

1. Build the model context — parsed markdown, table grids, and (conditionally)
   source images.
2. Call the model with a schema-constrained request.
3. Parse every reading into its canonical type, failing loudly on any that will not.
4. Wrap each value in `Extracted[T]` with confidence, method, and a source region
   resolved from the parse, so the UI can highlight where it came from.

Images are attached **conditionally**, not always. Sending page images on every
invoice would multiply token cost on the common case where a digital text layer is
already exact. They are attached when they can actually add information: the parse
came from OCR, or the document contains embedded pictures.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, TypeGuard

from pydantic import BaseModel, ConfigDict, Field

from ap_agent.agent.prompts.extraction import (
    PROMPT_VERSION,
    build_system_prompt,
    build_user_prompt,
)
from ap_agent.core.canonical import BankDetails, Invoice, InvoiceLine, InvoiceSource
from ap_agent.core.primitives import (
    CurrencyError,
    Extracted,
    ExtractionMethod,
    Money,
    SourceRegion,
)
from ap_agent.errors import APAgentError, ErrorContext, ExtractionError
from ap_agent.extract.bedrock import ModelClient
from ap_agent.extract.schema import FieldReading, LineReading, RawInvoiceExtraction
from ap_agent.ingest.parsing import ParsedDocument, ParseStrategy
from ap_agent.mapping.labels import RecoveredLabel, resolve_label

# Cap on attached images. A long scanned document could otherwise attach dozens of
# page images and blow the context budget (FR-15.3) on a single invoice.
MAX_ATTACHED_IMAGES = 4


class ExtractionOutcome(BaseModel):
    """An extraction result plus what it cost and how it was produced."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    invoice: Invoice
    raw: RawInvoiceExtraction
    prompt_version: str
    model_id: str
    duration_ms: float = Field(ge=0)
    input_tokens: int = 0
    output_tokens: int = 0
    images_attached: int = 0
    parse_strategy: ParseStrategy = ParseStrategy.STRUCTURAL
    escalated_to_ocr: bool = False
    attempts_used: int = 1
    """How many model calls this extraction took, including failed repairs.

    A confidence signal in its own right (`extract.confidence`): a reading the
    model produced validly on the first try is not the same quality as one that
    needed a schema-repair round-trip to become valid at all.
    """

    observed_labels: dict[str, RecoveredLabel] = Field(default_factory=dict)
    """Canonical field -> the label printed beside it, for alias learning (FR-3.2).

    Recovered from the parse where possible and from the model only where the parse
    cannot reach (see `mapping.labels`). Each entry carries how it was obtained, so
    the human confirming an alias rule can weigh a geometric match differently from
    an unverifiable model report.
    """

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def label_candidates(self, *, min_confidence: float = 0.0) -> dict[str, str]:
        """Printed label -> canonical field, filtered by attribution confidence.

        The lookup direction alias learning needs: given a label seen on a future
        invoice, which field does it mean.
        """
        return {
            label.text: field
            for field, label in self.observed_labels.items()
            if label.confidence >= min_confidence
        }


def render_tables(parsed: ParsedDocument) -> str:
    """Render detected tables as pipe-delimited grids.

    Column structure is preserved explicitly because flattening it is what destroys
    line-item meaning: a bare "20" is uninterpretable without knowing it sat under
    "Qty".
    """
    if not parsed.tables:
        return ""

    blocks: list[str] = []
    for index, table in enumerate(parsed.tables, start=1):
        if table.is_empty:
            continue
        lines = [f"Table {index} ({table.num_rows} rows x {table.num_cols} cols)"]
        if table.header:
            lines.append(" | ".join(table.header))
            lines.append("-" * 60)
        lines.extend(" | ".join(row) for row in table.rows)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def should_attach_images(parsed: ParsedDocument) -> bool:
    """Whether source images can add information.

    Attaching them always would multiply cost on the common case (an exact digital
    text layer) for no gain. They help when the text is a reconstruction, or when
    content sits inside a picture.
    """
    return parsed.strategy is ParseStrategy.OCR or bool(parsed.picture_regions)


class InvoiceExtractor:
    """Turns a parsed document into a canonical invoice."""

    def __init__(self, model: ModelClient) -> None:
        self._model = model

    def extract(
        self,
        parsed: ParsedDocument,
        *,
        tenant_id: str,
        document_id: str,
        invoice_id: str,
        source: InvoiceSource = InvoiceSource.UPLOAD,
        page_images: list[tuple[str, bytes]] | None = None,
        received_at: datetime | None = None,
    ) -> ExtractionOutcome:
        started = time.perf_counter()

        images: list[tuple[str, bytes]] = []
        if page_images and should_attach_images(parsed):
            images = page_images[:MAX_ATTACHED_IMAGES]

        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(
            markdown=parsed.markdown,
            tables_rendered=render_tables(parsed),
            parse_strategy=parsed.strategy.value,
            has_images=bool(images),
            escalated_to_ocr=parsed.escalated_to_ocr,
        )

        context = ErrorContext(
            stage="extract.model",
            tenant_id=tenant_id,
            document_id=document_id,
            inputs={"prompt_version": PROMPT_VERSION},
        )

        try:
            raw, usage = self._model.extract_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                images=images,
                schema=RawInvoiceExtraction,
            )
        except APAgentError:
            # A first-party error already carries its honest class and message
            # (e.g. a CredentialsError for an expired token, or an ExtractionError
            # for a genuine schema failure). Re-wrapping it as "the model did not
            # return a valid extraction" would relabel an auth failure as a reading
            # failure — the exact misdiagnosis INC-019 fixes. Let it propagate.
            raise
        except Exception as exc:
            # A schema-validation failure arrives here. It is FATAL: we do not
            # retry a malformed extraction into existence, and we certainly do not
            # accept a partial one.
            raise ExtractionError(
                "The model did not return a valid extraction for "
                f"{document_id!r}. Refusing to proceed with an unvalidated reading.",
                context=context,
                cause=exc,
            ) from exc

        if raw is None:
            raise ExtractionError(
                f"The model returned no parsed extraction for {document_id!r}.",
                context=context,
            )
        if not isinstance(raw, RawInvoiceExtraction):
            raise ExtractionError(
                f"Expected RawInvoiceExtraction, got {type(raw).__name__}.",
                context=context,
            )

        labels = resolve_labels(raw, parsed)

        invoice = self._to_canonical(
            raw,
            parsed=parsed,
            labels=labels,
            tenant_id=tenant_id,
            document_id=document_id,
            invoice_id=invoice_id,
            source=source,
            received_at=received_at,
        )

        return ExtractionOutcome(
            invoice=invoice,
            raw=raw,
            prompt_version=PROMPT_VERSION,
            model_id=getattr(self._model, "model_id", "unknown"),
            duration_ms=(time.perf_counter() - started) * 1000.0,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            images_attached=len(images),
            parse_strategy=parsed.strategy,
            escalated_to_ocr=parsed.escalated_to_ocr,
            attempts_used=int(usage.get("attempts_used", 1)),
            observed_labels=labels,
        )

    # ------------------------------------------------------------- conversion

    def _to_canonical(
        self,
        raw: RawInvoiceExtraction,
        *,
        parsed: ParsedDocument,
        labels: dict[str, RecoveredLabel],
        tenant_id: str,
        document_id: str,
        invoice_id: str,
        source: InvoiceSource,
        received_at: datetime | None,
    ) -> Invoice:
        ctx = ErrorContext(
            stage="extract.convert", tenant_id=tenant_id, document_id=document_id
        )
        method = (
            ExtractionMethod.OCR
            if parsed.strategy is ParseStrategy.OCR
            else ExtractionMethod.PARSED_STRUCTURE
        )

        currency = _require_currency(raw.currency, ctx)

        def wrap[T](value: T, reading: FieldReading, field: str) -> Extracted[T]:
            return _wrap(value, reading, parsed, method, labels.get(field))

        invoice_number = wrap(
            _require_text(raw.invoice_number, "invoice_number", ctx),
            raw.invoice_number,
            "invoice_number",
        )
        invoice_date = wrap(
            _require_date(raw.invoice_date, "invoice_date", ctx),
            raw.invoice_date,
            "invoice_date",
        )
        vendor_name = wrap(
            _require_text(raw.vendor_name, "vendor_name", ctx),
            raw.vendor_name,
            "vendor_name",
        )
        currency_field = wrap(currency, raw.currency, "currency")
        subtotal = wrap(
            _require_money(raw.subtotal, currency, "subtotal", ctx),
            raw.subtotal,
            "subtotal",
        )
        total_amount = wrap(
            _require_money(raw.total_amount, currency, "total_amount", ctx),
            raw.total_amount,
            "total_amount",
        )

        tax_amount = (
            wrap(
                _require_money(raw.tax_amount, currency, "tax_amount", ctx),
                raw.tax_amount,
                "tax_amount",
            )
            if _present(raw.tax_amount)
            else None
        )
        due_date = (
            wrap(_require_date(raw.due_date, "due_date", ctx), raw.due_date, "due_date")
            if _present(raw.due_date)
            else None
        )
        payment_terms = (
            wrap(raw.payment_terms.value.strip(), raw.payment_terms, "payment_terms")
            if _present(raw.payment_terms)
            else None
        )
        # Absence is a business state (non-PO invoice), not missing data — so a
        # null here is preserved rather than defaulted.
        po_reference = (
            wrap(raw.po_reference.value.strip(), raw.po_reference, "po_reference")
            if _present(raw.po_reference)
            else None
        )

        remit_to = self._build_remit_to(raw, parsed, method)

        lines = tuple(
            self._to_canonical_line(reading, currency, method, parsed, ctx)
            for reading in raw.lines
        )

        return Invoice(
            invoice_id=invoice_id,
            tenant_id=tenant_id,
            document_id=document_id,
            source=source,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            vendor_name=vendor_name,
            currency=currency_field,
            subtotal=subtotal,
            total_amount=total_amount,
            tax_amount=tax_amount,
            due_date=due_date,
            payment_terms=payment_terms,
            po_reference=po_reference,
            remit_to=remit_to,
            lines=lines,
            received_at=received_at,
        )

    def _build_remit_to(
        self, raw: RawInvoiceExtraction, parsed: ParsedDocument, method: ExtractionMethod
    ) -> Extracted[BankDetails] | None:
        last4 = raw.remit_to_account_last4
        bank = raw.remit_to_bank_name
        if not _present(last4) and not _present(bank):
            return None

        digits = (last4.value.strip() if _present(last4) else "") or None
        if digits is not None:
            # Defence in depth: the prompt forbids full account numbers, but a
            # prompt is not an enforcement mechanism (FR-15.7). Keep the last four.
            digits = "".join(ch for ch in digits if ch.isdigit())[-4:] or None

        details = BankDetails(
            account_number_last4=digits,
            bank_name=bank.value.strip() if _present(bank) else None,
        )
        # One of the two is present (guarded above); prefer the account digits as
        # the provenance anchor since that is the value the change check compares.
        anchor = last4 if _present(last4) else bank
        if anchor is None:  # pragma: no cover - unreachable given the guard above
            return None
        return _wrap(
            details, anchor, parsed, method, resolve_label(parsed, anchor.value, anchor.printed_label)
        )

    def _to_canonical_line(
        self,
        reading: LineReading,
        currency: str,
        method: ExtractionMethod,
        parsed: ParsedDocument,
        ctx: ErrorContext,
    ) -> InvoiceLine:
        region = parsed.find_region_containing(reading.description)
        source_region = region.to_source_region() if region else None

        def wrap[T](value: T) -> Extracted[T]:
            return Extracted[T](
                value=value,
                confidence=reading.confidence,
                method=method,
                region=source_region,
            )

        quantity_text, split_unit = _split_quantity_unit(reading.quantity)
        unit_of_measure = reading.unit_of_measure or split_unit

        return InvoiceLine(
            line_number=reading.line_number,
            description=wrap(reading.description),
            quantity=wrap(_parse_decimal(quantity_text, f"line {reading.line_number} quantity", ctx)),
            unit_price=wrap(
                _parse_money(reading.unit_price, currency, f"line {reading.line_number} unit_price", ctx)
            ),
            line_total=wrap(
                _parse_money(reading.line_total, currency, f"line {reading.line_number} line_total", ctx)
            ),
            unit_of_measure=wrap(unit_of_measure) if unit_of_measure else None,
        )


# ------------------------------------------------------------------- helpers


def _present(reading: FieldReading | None) -> TypeGuard[FieldReading]:
    """Whether an optional reading carries a usable value.

    A `TypeGuard` rather than a plain bool so the type checker narrows at every
    call site. Without it each caller would need its own redundant `is not None`
    check, and one of them would eventually be forgotten.
    """
    return reading is not None and reading.is_present


def resolve_labels(
    raw: RawInvoiceExtraction, parsed: ParsedDocument
) -> dict[str, RecoveredLabel]:
    """Printed label for each present header field, structure preferred.

    A separate pass rather than inline work during conversion, because conversion can
    abort partway on an unparseable amount and label recovery is diagnostic
    information we want regardless of whether the invoice converts.
    """
    resolved: dict[str, RecoveredLabel] = {}
    for field, reading in raw.header_readings().items():
        label = resolve_label(parsed, reading.value, reading.printed_label)
        if label is not None:
            resolved[field] = label
    return resolved


def _wrap[T](
    value: T,
    reading: FieldReading,
    parsed: ParsedDocument,
    method: ExtractionMethod,
    label: RecoveredLabel | None = None,
) -> Extracted[T]:
    """Attach confidence, method, source region, and printed label."""
    region: SourceRegion | None = None
    found = parsed.find_region_containing(reading.value.strip())
    if found is not None:
        region = found.to_source_region()

    return Extracted[T](
        value=value,
        confidence=reading.confidence,
        method=method,
        region=region,
        source_label=label.text if label is not None else None,
    )


def _require_text(reading: FieldReading, field: str, ctx: ErrorContext) -> str:
    text = reading.value.strip()
    if not text:
        raise ExtractionError(
            f"Required field {field!r} was not readable. Refusing to substitute a "
            "placeholder for a value that decides a payment.",
            context=ctx,
        )
    return text


def _require_currency(reading: FieldReading, ctx: ErrorContext) -> str:
    code = reading.value.strip().upper()
    if not code:
        raise ExtractionError(
            "Currency was not readable. Every amount is meaningless without it, "
            "and guessing a default would risk paying the wrong sum.",
            context=ctx,
        )
    return code


def _require_date(reading: FieldReading, field: str, ctx: ErrorContext) -> date:
    text = reading.value.strip()
    if not text:
        raise ExtractionError(f"Required date {field!r} was not readable.", context=ctx)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ExtractionError(
            f"Field {field!r} is not an ISO date: {text!r}. Not reinterpreted, "
            "because an ambiguous date could shift a payment due date.",
            context=ctx,
            cause=exc,
        ) from exc


# A quantity cell that prints its unit beside the number: "10 EA", "2.5 KG",
# "3 HR", "12 units". The number is captured separately from a trailing
# alphabetic unit token so each can be recorded where it belongs.
_QUANTITY_WITH_UNIT: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<qty>[-+]?[\d,]*\.?\d+)\s+(?P<unit>[A-Za-z][A-Za-z.]*)\s*$"
)


def _split_quantity_unit(text: str) -> tuple[str, str | None]:
    """Separate a quantity string from a unit printed in the same cell.

    A quantity column commonly reads "10 EA" or "2.5 KG": one printed cell holding
    two distinct values. The model is asked to keep them apart (rule 12), but does
    so inconsistently, so this splits them deterministically as defence in depth —
    prompts guide, code enforces (FR-15.7).

    This is not coercion. Both the number and the unit are literally printed on the
    page; separating them recovers exactly what is there, the same class of
    operation as stripping a currency symbol from an amount. It is deliberately
    narrow: only a bare number followed by a single alphabetic unit token splits.
    Anything else — "10 EA 20", "N/A", "" — is returned untouched so a genuinely
    unreadable quantity still fails loudly in `_parse_decimal` rather than being
    silently reshaped into something parseable.
    """
    match = _QUANTITY_WITH_UNIT.match(text)
    if match is None:
        return text, None
    return match.group("qty"), match.group("unit")


def _parse_decimal(text: str, label: str, ctx: ErrorContext) -> Decimal:
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        raise ExtractionError(f"{label} was not readable.", context=ctx)
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ExtractionError(
            f"{label} is not a decimal: {text!r}.", context=ctx, cause=exc
        ) from exc


def _parse_money(text: str, currency: str, label: str, ctx: ErrorContext) -> Money:
    amount = _parse_decimal(text, label, ctx)
    try:
        # `Money` refuses floats and unsupported currencies; both surface here as a
        # fatal extraction failure rather than a coerced value.
        return Money(amount=amount, currency=currency)
    except (CurrencyError, ValueError) as exc:
        raise ExtractionError(
            f"{label} could not be represented as {currency} money: {text!r}.",
            context=ctx,
            cause=exc,
        ) from exc


def _require_money(
    reading: FieldReading | None, currency: str, field: str, ctx: ErrorContext
) -> Money:
    if reading is None or not reading.is_present:
        raise ExtractionError(f"Required amount {field!r} was not readable.", context=ctx)
    return _parse_money(reading.value, currency, field, ctx)


def field_provenance(outcome: ExtractionOutcome) -> dict[str, Any]:
    """Per-field provenance for the `invoices.field_provenance` JSONB column.

    Persisted so a completed run can be audited without re-running extraction —
    which on a degraded scan could produce different values than the run under
    review.
    """
    invoice = outcome.invoice
    provenance: dict[str, Any] = {
        "prompt_version": outcome.prompt_version,
        "model_id": outcome.model_id,
        "images_attached": outcome.images_attached,
        "fields": {},
    }

    named: dict[str, Extracted[Any] | None] = {
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "vendor_name": invoice.vendor_name,
        "currency": invoice.currency,
        "subtotal": invoice.subtotal,
        "total_amount": invoice.total_amount,
        "tax_amount": invoice.tax_amount,
        "due_date": invoice.due_date,
        "payment_terms": invoice.payment_terms,
        "po_reference": invoice.po_reference,
    }

    for name, extracted in named.items():
        if extracted is None:
            continue
        provenance["fields"][name] = {
            "confidence": extracted.confidence,
            "method": extracted.method.value,
            "source_label": extracted.source_label,
            "element_ref": extracted.region.element_ref if extracted.region else None,
            "page": extracted.region.page if extracted.region else None,
        }

    return provenance
