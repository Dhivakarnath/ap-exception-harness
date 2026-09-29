"""Field label vocabulary — the semantic-mapping stress source.

Real vendors label the same field a dozen different ways. "Grand Total",
"Amount Payable", "Balance Due", and "TOTAL DUE" all mean `total_amount`. An
extraction pipeline that only recognises one spelling looks excellent in a demo
and collapses on contact with a second vendor.

These variants feed two things:

* the `LABEL_VARIATION` failure mode, which stresses mapping directly;
* the ordinary cases, which rotate through vendor-specific label sets so no
  single spelling dominates the dataset and inflates the score.

The canonical target for each group is the field name in
`core.canonical.Invoice`, so this file doubles as the expected contents of the
`field_aliases` table (FR-3.1).
"""

from __future__ import annotations

from typing import Final

# canonical field -> plausible printed labels, commonest first.
LABEL_VARIANTS: Final[dict[str, tuple[str, ...]]] = {
    "invoice_number": (
        "Invoice Number",
        "Invoice No.",
        "Invoice #",
        "Document Number",
        "Bill Number",
        "Reference",
        "Our Ref",
        "Tax Invoice No",
    ),
    "invoice_date": (
        "Invoice Date",
        "Date",
        "Date of Issue",
        "Issued",
        "Document Date",
        "Billing Date",
        "Tax Point Date",
    ),
    "due_date": (
        "Due Date",
        "Payment Due",
        "Pay By",
        "Payment Due Date",
        "Net Due",
    ),
    "po_reference": (
        "PO Number",
        "Purchase Order",
        "P.O. #",
        "Your Order No",
        "Customer PO",
        "Order Reference",
        "PO/Contract",
    ),
    "subtotal": (
        "Subtotal",
        "Net Amount",
        "Sub-Total",
        "Total Excl. Tax",
        "Goods Value",
        "Amount Before Tax",
    ),
    "tax_amount": (
        "Tax",
        "VAT",
        "Sales Tax",
        "GST",
        "Tax Amount",
        "Tax (8.25%)",
    ),
    # Multi-jurisdiction / statutory levy rows that appear alongside a primary
    # tax line in the MULTI_LINE_TAX and REVERSE_CHARGE_VAT modes. Kept as
    # aliases of `tax_amount` because they all map to the same canonical field.
    "tax_secondary": (
        "State Tax",
        "City Tax",
        "Environmental Levy",
        "Duty",
        "Surcharge",
        "GST",
    ),
    "total_amount": (
        "Total",
        "Total Due",
        "Grand Total",
        "Amount Payable",
        "Balance Due",
        "Invoice Total",
        "Amount Due",
        "TOTAL (USD)",
    ),
    "payment_terms": (
        "Payment Terms",
        "Terms",
        "Terms of Payment",
        "Payment Conditions",
    ),
    "vendor_name": (
        "From",
        "Supplier",
        "Vendor",
        "Remit To",
        "Billed By",
    ),
}

# Line-table column headers.
COLUMN_VARIANTS: Final[dict[str, tuple[str, ...]]] = {
    "description": ("Description", "Item", "Details", "Product / Service", "Particulars"),
    "quantity": ("Qty", "Quantity", "Units", "No.", "Count"),
    "unit_price": ("Unit Price", "Rate", "Price", "Price / Unit", "Unit Cost"),
    "line_total": ("Amount", "Total", "Line Total", "Value", "Extended"),
}

# Deliberately awkward labels reserved for the LABEL_VARIATION mode: correct but
# unusual phrasings that a naive keyword matcher will miss.
ADVERSARIAL_LABELS: Final[dict[str, tuple[str, ...]]] = {
    "invoice_number": ("Voucher Ident", "Statement Ref", "Doc-Nr"),
    "invoice_date": ("Raised On", "Dated", "Period Ending"),
    "po_reference": ("Against Order", "Buyer Reference", "Req. No"),
    "subtotal": ("Chargeable Value", "Net of Taxes"),
    "tax_amount": ("Statutory Levy", "Output Tax"),
    "total_amount": ("Please Remit", "Settlement Amount", "Payable Now"),
    "payment_terms": ("Settlement Terms", "Credit Period"),
}


def label_for(field: str, variant_index: int, *, adversarial: bool = False) -> str:
    """Pick a label deterministically.

    Indexing rather than random choice so a case's rendered labels are a pure
    function of its position in the dataset, which keeps generation reproducible.
    """
    if adversarial and field in ADVERSARIAL_LABELS:
        pool = ADVERSARIAL_LABELS[field]
    else:
        pool = LABEL_VARIANTS.get(field, (field.replace("_", " ").title(),))
    return pool[variant_index % len(pool)]


def column_label_for(column: str, variant_index: int) -> str:
    pool = COLUMN_VARIANTS.get(column, (column.title(),))
    return pool[variant_index % len(pool)]


def all_known_labels() -> dict[str, tuple[str, ...]]:
    """Every label we might print, per canonical field.

    Used by the alias-coverage test: the dataset must not print a label that the
    expected alias table has no entry for, or extraction would be graded against
    a mapping it was never given.
    """
    merged: dict[str, tuple[str, ...]] = {}
    for field, variants in LABEL_VARIANTS.items():
        merged[field] = variants + ADVERSARIAL_LABELS.get(field, ())
    return merged
