"""Demo: parse and extract one invoice end to end with live Nova Lite.

    uv run python scripts/extract_demo.py                      # clean digital PDF
    uv run python scripts/extract_demo.py ocr_noise-000.pdf    # degraded scan
    uv run python scripts/extract_demo.py --all                # one per format

Requires AWS credentials with Bedrock access. Shows the extracted canonical
invoice, per-field confidence, and where each value came from — which is the point:
extraction should be inspectable, not asserted.
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

REPO_ROOT = Path(__file__).resolve().parents[2]
INVOICES = REPO_ROOT / "datasets" / "generated" / "invoices"

from ap_agent.core.primitives import Extracted  # noqa: E402
from ap_agent.extract.bedrock import BedrockModelClient  # noqa: E402
from ap_agent.extract.confidence import derive_confidence  # noqa: E402
from ap_agent.extract.extractor import InvoiceExtractor, should_attach_images  # noqa: E402
from ap_agent.ingest.parsing import parse_path, render_page_images  # noqa: E402

MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}


def _show(label: str, extracted: Extracted[object] | None) -> None:
    if extracted is None:
        print(f"  {label:16} (absent)")
        return
    ref = extracted.region.element_ref if extracted.region else "-"
    printed = extracted.source_label or "-"
    print(
        f"  {label:16} {str(extracted.value):<28} "
        f"conf={extracted.confidence:.2f}  label={printed!r:<22} ref={ref}"
    )


def run(path: Path) -> None:
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise SystemExit(f"Unsupported extension: {path.suffix}")

    print("=" * 86)
    print(f"  {path.name}")
    print("=" * 86)

    parsed = parse_path(path, media_type=media_type)
    print(
        f"  parse: {parsed.strategy.value} "
        f"(escalated={parsed.escalated_to_ocr}) "
        f"{parsed.text_length} chars, {len(parsed.tables)} tables, "
        f"{len(parsed.picture_regions)} pictures"
    )

    # Attach page images when they can add information the text pipeline lost.
    page_images: list[tuple[str, bytes]] = []
    if should_attach_images(parsed):
        page_images = render_page_images(path, media_type=media_type)

    extractor = InvoiceExtractor(BedrockModelClient())
    outcome = extractor.extract(
        parsed,
        tenant_id="manufacturing-demo",
        document_id=f"demo-{path.stem}",
        invoice_id=f"inv-{path.stem}",
        page_images=page_images,
    )

    inv = outcome.invoice
    print(
        f"  model: {outcome.model_id} | prompt: {outcome.prompt_version} | "
        f"{outcome.duration_ms:.0f} ms | tokens in/out "
        f"{outcome.input_tokens}/{outcome.output_tokens} | images {outcome.images_attached}"
    )
    print()
    print("  --- extracted fields ---")
    _show("invoice_number", inv.invoice_number)
    _show("invoice_date", inv.invoice_date)
    _show("vendor_name", inv.vendor_name)
    _show("currency", inv.currency)
    _show("subtotal", inv.subtotal)
    _show("tax_amount", inv.tax_amount)
    _show("total_amount", inv.total_amount)
    _show("po_reference", inv.po_reference)
    _show("due_date", inv.due_date)
    _show("payment_terms", inv.payment_terms)

    if inv.remit_to is not None:
        details = inv.remit_to.value
        print(
            f"  {'remit_to':16} last4={details.account_number_last4} "
            f"bank={details.bank_name!r} conf={inv.remit_to.confidence:.2f}"
        )

    print()
    print(f"  --- {len(inv.lines)} line(s) ---")
    for line in inv.lines:
        print(
            f"  {line.line_number}. {line.description.value[:38]:<38} "
            f"qty={line.quantity.value} @ {line.unit_price.value} "
            f"= {line.line_total.value}  conf={line.min_confidence:.2f}"
        )

    print()
    print(f"  non-PO invoice      : {inv.is_non_po}")
    print(f"  header confidence   : {inv.header_confidence:.3f}")
    print(f"  overall confidence  : {inv.overall_confidence:.3f}")
    objective = derive_confidence(outcome)
    print(
        f"  objective confidence: {objective.adjusted:.3f} "
        f"(model reported {objective.model_reported:.3f}, attempts {outcome.attempts_used})"
    )
    for factor in objective.factors:
        print(f"    x{factor.multiplier:.3f} {factor.name:20} {factor.reasoning}")
    line_sum = inv.computed_line_sum()
    print(f"  line sum            : {line_sum}  (printed subtotal {inv.subtotal.value})")
    print(f"  currencies present  : {sorted(inv.currencies_present())}")

    if outcome.observed_labels:
        print()
        print("  --- printed labels recovered (alias-learning candidates) ---")
        for field, label in sorted(outcome.observed_labels.items()):
            print(
                f"    {label.text!r:26} -> {field:16} "
                f"via {label.source.value:14} conf={label.confidence:.2f}"
            )

    if outcome.raw.unreadable_regions:
        print()
        print("  --- model reported unreadable ---")
        for note in outcome.raw.unreadable_regions:
            print(f"    {note}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filename", nargs="?", default="clean_touchless-000.pdf")
    parser.add_argument("--all", action="store_true", help="One example per format.")
    args = parser.parse_args(argv)

    if not INVOICES.is_dir():
        print("No dataset found. Run `make dataset-quick` first.", file=sys.stderr)
        return 1

    targets = (
        ["clean_touchless-000.pdf", "ocr_noise-000.pdf", "image_only-000.png"]
        if args.all
        else [args.filename]
    )
    for name in targets:
        candidate = Path(name)
        path = candidate if candidate.is_absolute() else INVOICES / name
        if not path.is_file():
            print(f"Not found: {path}", file=sys.stderr)
            return 1
        run(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
