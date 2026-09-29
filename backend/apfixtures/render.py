"""Invoice renderers.

Three output formats, each exercising a different part of the parsing path:

* `PDF_TEXT` — a digital PDF with a real text layer and a real table. Docling
  parses structure directly. This is the easy path and most of the dataset.
* `PDF_SCANNED` — the same page rasterised, degraded, and re-embedded with **no
  text layer**. Forces the OCR route (FR-2.6).
* `IMAGE` — a bare PNG, as though someone photographed the invoice.

The degradation is not cosmetic. Blur, noise, rotation, and JPEG-style
compression artefacts are what make extraction confidence drop, and confidence is
what gates auto-approval. A dataset without genuinely degraded inputs would let
the confidence threshold pass untested.

Determinism: all randomness comes from a caller-supplied `numpy` Generator
seeded per case, so a case renders identically on every run.
"""

from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph

from apfixtures.labels import column_label_for, label_for
from apfixtures.spec import InvoiceCase, InvoiceContent, RenderFormat

PAGE_W, PAGE_H = LETTER
MARGIN = 0.75 * inch


def _fmt(amount: Decimal) -> str:
    """Render money with thousands separators, as a real invoice would.

    Negatives render with a leading minus (credit notes); this is deliberate,
    so a credit note's amounts read as negative on the page exactly as the
    ground truth records them.
    """
    return f"{amount:,.2f}"


def _fmt_qty(qty: Decimal) -> str:
    normalised = qty.normalize()
    text = format(normalised, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# ------------------------------------------------------------------ PDF (text)


def _draw_logo_block(c: pdf_canvas.Canvas, x: float, top_y: float) -> None:
    """A simple vendor-logo mark that crowds the header.

    A filled rounded square with the vendor's initial — enough to occupy the
    space a real logo would and force header text to share the region, without
    depending on an external image asset (which would break clone-and-run and
    make the render non-deterministic if the asset ever changed).
    """
    size = 42
    c.saveState()
    c.setFillColor(colors.HexColor("#2B4C7E"))
    c.roundRect(x, top_y - size + 12, size, size, 6, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 24)
    c.drawCentredString(x + size / 2, top_y - size / 2 + 4, "A")
    c.restoreState()


def _continue_line_table_on_new_page(
    c: pdf_canvas.Canvas,
    remaining: tuple,  # type: ignore[type-arg]
    money_prefix: str,
    col_desc: float,
    col_qty: float,
    col_price: float,
    col_total: float,
) -> float:
    """Render remaining line items on a fresh page, returning the new y.

    Used by the multi-page mode: a real long invoice continues its line table
    onto page two with a repeated header row, and the totals block then follows
    the last line wherever it lands.
    """
    c.showPage()
    y: float = PAGE_H - MARGIN

    c.setFont("Helvetica-Oblique", 9)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawString(MARGIN, y, "Line items continued")
    c.setFillColor(colors.black)
    y -= 20

    c.setFillColor(colors.HexColor("#EFEFEF"))
    c.rect(MARGIN - 4, y - 4, PAGE_W - 2 * MARGIN + 8, 16, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(col_desc, y, "Description")
    c.drawRightString(col_qty, y, "Qty")
    c.drawRightString(col_price, y, "Unit Price")
    c.drawRightString(col_total, y, "Amount")
    y -= 18

    c.setFont("Helvetica", 8.5)
    for line in remaining:
        c.drawString(col_desc, y, line.description[:60])
        c.drawRightString(col_qty, y, f"{_fmt_qty(line.quantity)} {line.unit_of_measure}")
        c.drawRightString(col_price, y, f"{money_prefix} {_fmt(line.unit_price)}")
        c.drawRightString(col_total, y, f"{money_prefix} {_fmt(line.line_total)}")
        y -= 13
        if y < MARGIN + 120:
            # Extremely long tables would need a third page; the dataset's
            # multi-page cases are sized to fit two, so this is a guard rather
            # than an expected path.
            break

    y -= 6
    c.setStrokeColor(colors.HexColor("#BBBBBB"))
    c.line(MARGIN + 3.2 * inch, y, PAGE_W - MARGIN, y)
    return float(y - 16)


def render_pdf_text(case: InvoiceCase, *, variant: int, adversarial_labels: bool) -> bytes:
    """Digital PDF with a selectable text layer and a real table."""
    content = case.content
    buffer = io.BytesIO()
    # invariant=1 suppresses the creation timestamp and random document ID that
    # reportlab embeds by default. Without it, every run produces different bytes
    # and the manifest's content hash — the mechanism that detects a stale
    # dataset — would be meaningless. See INC-003.
    c = pdf_canvas.Canvas(buffer, pagesize=LETTER, invariant=1)
    c.setTitle(f"Invoice {content.invoice_number}")
    c.setAuthor(content.vendor_name_printed)

    y = PAGE_H - MARGIN

    # --- vendor block -------------------------------------------------------
    # A logo block, when present, is drawn first and the vendor name is nudged
    # to sit beside/under it — the way a real letterhead crowds the header
    # (the LOGO_OVERLAP mode).
    name_x = MARGIN
    if content.has_logo:
        _draw_logo_block(c, MARGIN, y)
        name_x = MARGIN + 52

    c.setFont("Helvetica-Bold", 16)
    c.drawString(name_x, y, content.vendor_name_printed)
    y -= 16
    c.setFont("Helvetica", 8.5)
    c.setFillColor(colors.HexColor("#444444"))
    for addr_line in ("1 Industrial Way", "Springfield, IL 62701", "accounts@vendor.example"):
        c.drawString(name_x, y, addr_line)
        y -= 10.5
    c.setFillColor(colors.black)

    # --- title --------------------------------------------------------------
    c.setFont("Helvetica-Bold", 22)
    title = "CREDIT NOTE" if content.is_credit_note else "INVOICE"
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN, title)

    # --- header fields ------------------------------------------------------
    header_y = PAGE_H - MARGIN - 34
    fields: list[tuple[str, str]] = [
        (label_for("invoice_number", variant, adversarial=adversarial_labels), content.invoice_number),
        (
            label_for("invoice_date", variant, adversarial=adversarial_labels),
            content.invoice_date.isoformat(),
        ),
    ]
    if content.due_date:
        fields.append(
            (
                label_for("due_date", variant, adversarial=adversarial_labels),
                content.due_date.isoformat(),
            )
        )
    if content.po_reference:
        fields.append(
            (
                label_for("po_reference", variant, adversarial=adversarial_labels),
                content.po_reference,
            )
        )
    if content.payment_terms:
        fields.append(
            (
                label_for("payment_terms", variant, adversarial=adversarial_labels),
                content.payment_terms,
            )
        )

    for label, value in fields:
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor("#555555"))
        c.drawRightString(PAGE_W - MARGIN - 110, header_y, f"{label}:")
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(PAGE_W - MARGIN, header_y, value)
        header_y -= 13

    y = min(y, header_y) - 18

    # --- bill-to ------------------------------------------------------------
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN, y, "Bill To")
    y -= 12
    c.setFont("Helvetica", 9)
    for addr_line in ("Manufacturing Demo Co", "Accounts Payable", "PO Box 4100, Peoria, IL 61601"):
        c.drawString(MARGIN, y, addr_line)
        y -= 11
    y -= 12

    # --- line table ---------------------------------------------------------
    col_desc = MARGIN
    col_qty = MARGIN + 3.55 * inch
    col_price = MARGIN + 4.75 * inch
    col_total = PAGE_W - MARGIN

    c.setFillColor(colors.HexColor("#EFEFEF"))
    c.rect(MARGIN - 4, y - 4, PAGE_W - 2 * MARGIN + 8, 16, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(col_desc, y, column_label_for("description", variant))
    c.drawRightString(col_qty, y, column_label_for("quantity", variant))
    c.drawRightString(col_price, y, column_label_for("unit_price", variant))
    c.drawRightString(col_total, y, column_label_for("line_total", variant))
    y -= 18

    line_ccy = content.line_currency_override or content.currency
    money_prefix = content.currency_symbol or line_ccy
    c.setFont("Helvetica", 8.5)
    for rendered_lines, line in enumerate(content.lines, start=1):
        c.drawString(col_desc, y, line.description[:60])
        c.drawRightString(col_qty, y, f"{_fmt_qty(line.quantity)} {line.unit_of_measure}")
        c.drawRightString(col_price, y, f"{money_prefix} {_fmt(line.unit_price)}")
        c.drawRightString(col_total, y, f"{money_prefix} {_fmt(line.line_total)}")
        y -= 13
        if y < MARGIN + 150:  # first page is full; continue on a second page
            remaining = content.lines[rendered_lines:]
            if remaining:
                y = _continue_line_table_on_new_page(
                    c, remaining, money_prefix, col_desc, col_qty, col_price, col_total
                )
            break

    y -= 6
    c.setStrokeColor(colors.HexColor("#BBBBBB"))
    c.line(MARGIN + 3.2 * inch, y, PAGE_W - MARGIN, y)
    y -= 16

    # --- totals -------------------------------------------------------------
    # One row per tax line rather than a single hardcoded tax row, so
    # multi-jurisdiction and reverse-charge invoices print every levy with its
    # own label. `effective_tax_lines` collapses to a single row for the common
    # single-tax case, so existing invoices render unchanged.
    totals: list[tuple[str, str, bool]] = [
        (
            label_for("subtotal", variant, adversarial=adversarial_labels),
            f"{money_prefix} {_fmt(content.subtotal)}",
            False,
        )
    ]
    for tax_line in content.effective_tax_lines:
        # A reverse-charge row keeps its printed label verbatim (it carries the
        # legal meaning); other tax rows rotate through the label vocabulary.
        if tax_line.is_reverse_charge or content.tax_lines:
            tax_label = tax_line.label
        else:
            tax_label = label_for("tax_amount", variant, adversarial=adversarial_labels)
        totals.append((tax_label, f"{money_prefix} {_fmt(tax_line.amount)}", False))

    totals.append(
        (
            label_for("total_amount", variant, adversarial=adversarial_labels),
            f"{money_prefix} {_fmt(content.total_amount)}",
            True,
        )
    )

    for label, value, emphasise in totals:
        c.setFont("Helvetica-Bold" if emphasise else "Helvetica", 10 if emphasise else 9)
        c.drawRightString(PAGE_W - MARGIN - 110, y, f"{label}:")
        c.drawRightString(PAGE_W - MARGIN, y, value)
        y -= 15

    # --- remit-to (the bank-detail-change surface) --------------------------
    if content.remit_to_last4:
        y -= 10
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(MARGIN, y, "Remittance Details")
        y -= 11
        c.setFont("Helvetica", 8.5)
        bank = content.remit_to_bank_name or "First National"
        c.drawString(MARGIN, y, f"Bank: {bank}")
        y -= 10
        c.drawString(MARGIN, y, f"Account ending: {content.remit_to_last4}")
        y -= 10

    if content.notes:
        y -= 8
        style = ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=8, leading=10)
        para = Paragraph(content.notes, style)
        _, height = para.wrap(PAGE_W - 2 * MARGIN, 60)
        para.drawOn(c, MARGIN, y - height)

    c.showPage()
    c.save()
    return buffer.getvalue()


# --------------------------------------------------------------- image render


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Best available font, falling back to PIL's bitmap default.

    A missing system font must not fail generation — but it does change glyph
    shapes, so the fallback is deliberate and quiet rather than an error.
    """
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render_image(
    case: InvoiceCase, *, variant: int, adversarial_labels: bool, rng: np.random.Generator
) -> bytes:
    """Render the invoice as a degraded raster image.

    Drawn directly with PIL rather than rasterising the PDF: that avoids a
    poppler/PyMuPDF system dependency, and gives precise control over the
    degradation that makes OCR genuinely hard.
    """
    content = case.content
    scale = case.dpi / 72.0
    width, height = int(PAGE_W * scale), int(PAGE_H * scale)

    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)

    f_title = _load_font(int(22 * scale))
    f_head = _load_font(int(11 * scale))
    f_body = _load_font(int(9 * scale))
    f_small = _load_font(int(8 * scale))

    m = int(MARGIN * scale)
    y = m

    name_x = m
    if content.has_logo:
        logo_size = int(42 * scale)
        draw.rounded_rectangle(
            [(m, y), (m + logo_size, y + logo_size)], radius=int(6 * scale), fill=70
        )
        draw.text((m + int(12 * scale), y + int(8 * scale)), "A", font=f_title, fill=245)
        name_x = m + logo_size + int(10 * scale)

    draw.text((name_x, y), content.vendor_name_printed, font=f_head, fill=30)
    title = "CREDIT NOTE" if content.is_credit_note else "INVOICE"
    draw.text((width - m - int(150 * scale), y), title, font=f_title, fill=20)
    y += int(30 * scale)

    rows: list[tuple[str, str]] = [
        (label_for("invoice_number", variant, adversarial=adversarial_labels), content.invoice_number),
        (
            label_for("invoice_date", variant, adversarial=adversarial_labels),
            content.invoice_date.isoformat(),
        ),
    ]
    if content.po_reference:
        rows.append(
            (
                label_for("po_reference", variant, adversarial=adversarial_labels),
                content.po_reference,
            )
        )
    for label, value in rows:
        draw.text((m, y), f"{label}: {value}", font=f_body, fill=45)
        y += int(14 * scale)

    y += int(10 * scale)
    line_ccy = content.line_currency_override or content.currency
    money_prefix = content.currency_symbol or line_ccy
    draw.text((m, y), column_label_for("description", variant), font=f_small, fill=60)
    draw.text((m + int(300 * scale), y), column_label_for("quantity", variant), font=f_small, fill=60)
    draw.text((m + int(370 * scale), y), column_label_for("line_total", variant), font=f_small, fill=60)
    y += int(16 * scale)

    # The image path is a single raster page, so a very long table is truncated
    # rather than paginated — the multi-page mode renders as PDF_TEXT, not here.
    for line in content.lines[:12]:
        draw.text((m, y), line.description[:52], font=f_body, fill=40)
        draw.text((m + int(300 * scale), y), _fmt_qty(line.quantity), font=f_body, fill=40)
        draw.text(
            (m + int(370 * scale), y), f"{money_prefix} {_fmt(line.line_total)}", font=f_body, fill=40
        )
        y += int(14 * scale)

    y += int(12 * scale)
    draw.line([(m + int(280 * scale), y), (width - m, y)], fill=140, width=max(1, int(scale)))
    y += int(12 * scale)

    draw.text(
        (m + int(280 * scale), y),
        f"{label_for('subtotal', variant, adversarial=adversarial_labels)}: "
        f"{money_prefix} {_fmt(content.subtotal)}",
        font=f_body,
        fill=40,
    )
    y += int(15 * scale)
    for tax_line in content.effective_tax_lines:
        if tax_line.is_reverse_charge or content.tax_lines:
            tax_label = tax_line.label
        else:
            tax_label = label_for("tax_amount", variant, adversarial=adversarial_labels)
        draw.text(
            (m + int(280 * scale), y),
            f"{tax_label}: {money_prefix} {_fmt(tax_line.amount)}",
            font=f_body,
            fill=40,
        )
        y += int(15 * scale)
    draw.text(
        (m + int(280 * scale), y),
        f"{label_for('total_amount', variant, adversarial=adversarial_labels)}: "
        f"{money_prefix} {_fmt(content.total_amount)}",
        font=f_head,
        fill=20,
    )
    y += int(20 * scale)

    if content.remit_to_last4:
        draw.text(
            (m, y),
            f"Remit to {content.remit_to_bank_name or 'First National'} "
            f"acct ending {content.remit_to_last4}",
            font=f_small,
            fill=60,
        )

    return _degrade(img, case=case, rng=rng)


def _degrade(img: Image.Image, *, case: InvoiceCase, rng: np.random.Generator) -> bytes:
    """Apply scan-like degradation and encode as PNG."""
    noise = case.noise_level

    if case.rotation_degrees:
        img = img.rotate(
            case.rotation_degrees, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255
        )

    if noise > 0:
        # Blur first: real scans lose sharpness before they gain grain.
        img = img.filter(ImageFilter.GaussianBlur(radius=0.4 + 1.6 * noise))

        arr = np.asarray(img, dtype=np.float32)

        # Gaussian sensor noise.
        arr += rng.normal(0.0, 6.0 + 34.0 * noise, size=arr.shape)

        # Uneven illumination — a photographed page is rarely evenly lit.
        rows, cols = arr.shape
        gradient = np.linspace(-14.0 * noise, 10.0 * noise, cols, dtype=np.float32)
        arr += gradient[None, :]

        # Speckle: isolated dark pixels, the artefact that makes OCR invent
        # characters rather than simply miss them.
        speckle_density = 0.0008 * noise
        mask = rng.random(arr.shape) < speckle_density
        arr[mask] = rng.uniform(0.0, 90.0, size=int(mask.sum()))

        arr = np.clip(arr, 0.0, 255.0)
        img = Image.fromarray(arr.astype(np.uint8), mode="L")

        if noise > 0.55:
            # Heavy cases lose resolution as well as clarity.
            small = img.resize(
                (max(1, img.width * 2 // 3), max(1, img.height * 2 // 3)),
                Image.Resampling.BILINEAR,
            )
            img = small.resize((img.width, img.height), Image.Resampling.BILINEAR)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


# ------------------------------------------------------------ scanned PDF


def render_pdf_scanned(
    case: InvoiceCase, *, variant: int, adversarial_labels: bool, rng: np.random.Generator
) -> bytes:
    """A PDF containing only a rasterised page — no text layer.

    This is what a real scanner produces, and it is the case that forces the OCR
    path. Docling cannot read a text layer that does not exist.
    """
    png = render_image(
        case, variant=variant, adversarial_labels=adversarial_labels, rng=rng
    )
    img = Image.open(io.BytesIO(png))

    buffer = io.BytesIO()
    c = pdf_canvas.Canvas(buffer, pagesize=LETTER, invariant=1)
    # No drawString calls: deliberately no extractable text.
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(img), 0, 0, width=PAGE_W, height=PAGE_H)
    c.showPage()
    c.save()
    return buffer.getvalue()


def render_case(
    case: InvoiceCase, *, variant: int, adversarial_labels: bool, rng: np.random.Generator
) -> bytes:
    """Render a case in its declared format."""
    if case.render_format is RenderFormat.PDF_TEXT:
        return render_pdf_text(case, variant=variant, adversarial_labels=adversarial_labels)
    if case.render_format is RenderFormat.PDF_SCANNED:
        return render_pdf_scanned(
            case, variant=variant, adversarial_labels=adversarial_labels, rng=rng
        )
    if case.render_format is RenderFormat.IMAGE:
        return render_image(case, variant=variant, adversarial_labels=adversarial_labels, rng=rng)
    raise ValueError(f"Unhandled render format: {case.render_format}")


def content_summary(content: InvoiceContent) -> str:
    """One-line description, for CLI output and manifest readability."""
    po = content.po_reference or "no-PO"
    return (
        f"{content.invoice_number} | {content.vendor_name_printed} | "
        f"{content.currency} {_fmt(content.total_amount)} | {po}"
    )
