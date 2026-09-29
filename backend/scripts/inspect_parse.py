"""Demo: parse an invoice and show the structure Docling recovered.

    uv run python scripts/inspect_parse.py                       # a clean invoice
    uv run python scripts/inspect_parse.py ocr_noise-000.pdf     # a degraded scan
    uv run python scripts/inspect_parse.py --all                 # one per format

This is the Slice 4 demo checkpoint: upload a messy PDF, see the parsed structure
and detected regions. It also makes the two-pass OCR decision visible, which is
otherwise buried in a log line.
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

from ap_agent.ingest.parsing import ParsedDocument, parse_path  # noqa: E402

MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}


def describe(path: Path) -> ParsedDocument:
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise SystemExit(f"Unsupported extension: {path.suffix}")

    doc = parse_path(path, media_type=media_type)

    print("=" * 78)
    print(f"  {path.name}")
    print("=" * 78)
    print(f"  strategy        : {doc.strategy.value}")
    print(f"  escalated to OCR: {doc.escalated_to_ocr}")
    print(f"  parser          : {doc.parser_name} {doc.parser_version}")
    print(f"  pages           : {doc.page_count}")
    print(f"  text length     : {doc.text_length} chars")
    print(f"  regions         : {len(doc.regions)}")
    print(f"  tables          : {len(doc.tables)}")
    print(f"  pictures        : {len(doc.picture_regions)}")
    print(f"  duration        : {doc.duration_ms:.0f} ms")

    print()
    print("  --- regions (normalised top-left coordinates) ---")
    print(f"  {'kind':16} {'top':>6} {'left':>6}  {'ref':14} text")
    for region in doc.regions[:14]:
        box = region.bbox
        top = f"{box.top:.3f}" if box else "  -  "
        left = f"{box.left:.3f}" if box else "  -  "
        text = region.text[:38].replace("\n", " ")
        print(f"  {region.kind.value:16} {top:>6} {left:>6}  {region.element_ref:14} {text}")
    if len(doc.regions) > 14:
        print(f"  ... {len(doc.regions) - 14} more")

    for table in doc.tables:
        print()
        print(f"  --- table {table.element_ref} ({table.num_rows}x{table.num_cols}) ---")
        if table.header:
            print("  " + " | ".join(f"{h:<20}" for h in table.header))
            print("  " + "-" * (23 * len(table.header)))
        for row in table.rows:
            print("  " + " | ".join(f"{c:<20}" for c in row))

    print()
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "filename",
        nargs="?",
        default="clean_touchless-000.pdf",
        help="File inside datasets/generated/invoices/, or an absolute path.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Parse one example of each render format (text PDF, scanned PDF, image).",
    )
    args = parser.parse_args(argv)

    if not INVOICES.is_dir():
        print(
            "No generated dataset found. Run `make dataset-quick` first.",
            file=sys.stderr,
        )
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
        describe(path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
