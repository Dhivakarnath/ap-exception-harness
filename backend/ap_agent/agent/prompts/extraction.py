"""Extraction prompt, versioned.

Structure follows FR-15: a stable **role** block, a versioned **operational**
block, and a **schema anchor** restated on every call. Keeping them separate means
a behavioural change cannot accidentally rewrite the model's identity, and CI can
correlate a prompt version bump with an eval delta (FR-15.1).

The operational instructions concentrate on the failure modes that actually cost
money in this domain:

* **Never invent a PO reference.** A fabricated one turns a non-PO invoice into a
  bogus three-way match against an unrelated order. Absence is a meaningful
  business state, not missing data.
* **Report low confidence honestly.** Confidence gates auto-approval. A model that
  reports 0.99 on a blurred total defeats the control entirely, so the prompt asks
  for calibration explicitly and gives concrete triggers for lowering it.
* **Never emit a full bank account number.** Only the last four digits are needed
  to detect a change, and full numbers should not be in our logs or database.
* **Do no arithmetic.** Totals are checked deterministically downstream. A model
  that "corrects" a subtotal would erase the very inconsistency the
  `math_integrity` check exists to catch.

Prompts guide; they never enforce. Every rule that matters is also checked in
harness code (FR-15.7).
"""

from __future__ import annotations

from typing import Final

# v1.1.0 — added the totals-block convention and the no-borrowed-labels rule after
#          observing two real failures on the fixture set.
# v1.2.0 — v1.1.0 over-corrected: two consecutive rules emphasising `null` taught the
#          model that null was the safe default, and it stopped reporting labels
#          entirely — including labels plainly present in the text. Alias learning
#          (FR-3.2) has no input without them. Rule 5 is now a positive obligation;
#          the null cases are narrowed to two named exceptions. See INC-004.
# v1.3.0 — multi-tax handling: on a multi-jurisdiction totals block (State + City
#          tax, VAT + levy) the model captured only the first tax row, so
#          subtotal + one-of-two-taxes did not reconcile against the total and the
#          confidence gate flagged a valid invoice. Rule 7 now instructs summing
#          all tax rows into tax_amount and itemising them in tax_breakdown, and
#          affirms that a 0.00 reverse-charge tax is a complete value. See INC-006.
# v1.3.1 — credit notes: the model read a negative subtotal/total but a positive
#          line total, making a valid credit note look like a line-sum mismatch.
#          Rule 9 now requires the sign to be preserved consistently across every
#          amount on a credit note. See INC-006.
# v1.3.2 — quantity/unit separation: the Qty column commonly prints the unit beside
#          the number ("10 EA", "2.5 KG"), and the model intermittently returned the
#          whole cell ("10 EA") as `quantity`, which is not a decimal and failed the
#          no-coercion parser loudly on otherwise clean invoices. Rule 12 now states
#          the numeric part is the quantity and the unit belongs in unit_of_measure.
#          The parser also splits it deterministically as defence in depth (a prompt
#          guides, code enforces — FR-15.7). See INC-019.
PROMPT_VERSION: Final[str] = "extraction/v1.3.2"

# --- role block: stable identity, changes rarely -----------------------------

ROLE: Final[str] = """\
You are an invoice data extraction component inside an accounts payable system. \
You read a supplied invoice document and report the values printed on it.

You are a reader, not an accountant and not an approver. You do not decide whether \
an invoice should be paid, you do not correct what is printed, and you do not \
compute values that are absent."""


# --- operational block: task rules, versioned --------------------------------

OPERATIONAL: Final[str] = """\
Rules, in priority order.

1. Report only what is printed. If a field is absent, return null for optional \
fields or an empty string for required ones. Never infer a value from context and \
present it as though it were printed.

2. Never invent a purchase order reference. Many invoices legitimately have none. \
Do not substitute a quote number, delivery note, contract number, or customer \
account number. If no purchase order is cited, po_reference must be null.

3. Do no arithmetic. Report subtotal, tax, and total exactly as printed even when \
they do not add up. Inconsistencies are checked downstream and are meaningful \
evidence. Silently fixing one destroys that evidence.

4. Calibrate confidence honestly. Confidence decides whether a human reviews this \
invoice, so an overconfident reading is worse than a cautious one. Lower confidence \
when text is blurred, skewed, or faint; when a digit could be misread (0/O, 1/l, \
5/S, 8/B); when a value is partially cut off; or when two candidate values exist \
and you had to choose.

5. Record the printed label for every value that has one. This is required, not \
optional. Vendors label the same field many ways — "Grand Total", "Amount Payable", \
and "Balance Due" all mean the total — and capturing the exact wording is how the \
system learns this vendor's format instead of re-guessing it on every invoice.

   A label counts when it sits immediately before or beside the value, whether or \
not it ends in a colon. In extracted text a label and its value are frequently on \
consecutive lines, like:

       Invoice Number:
       INV-12345

   That is a label. Report printed_label "Invoice Number:" exactly as printed, \
including any trailing colon. Expect most header fields — invoice number, invoice \
date, due date, PO number, payment terms — to have one, and report it.

6. Two narrow exceptions to rule 5, and only these two.

   First, set printed_label to null when a value genuinely has no adjacent label. \
A vendor name printed as a letterhead heading usually has none.

   Second, do not borrow a table column header such as "Amount", "Unit Price", or \
"Qty" and present it as a header field's label. A borrowed label is worse than \
none, because the system may promote it into a permanent mapping rule for this \
vendor. Use null instead.

   Neither exception licenses null as a default. If a label is visible, report it.

7. Reading the totals block. Layout extraction often loses the "Subtotal", "Tax", \
and "Total" labels while keeping their amounts, so you may see two to four \
unlabelled amounts at the end of the line table with empty description, quantity, \
and price cells. These are the totals block, not line items. By near-universal \
invoice convention they appear in this order:

   - subtotal (goods value before tax)
   - one or more tax or charge lines
   - total (the largest, and equal to subtotal plus the taxes)

   Use that ordering together with arithmetic plausibility to assign them: if three \
unlabelled amounts appear and the first plus the second equals the third, they are \
subtotal, tax, and total. Exclude all of them from lines.

   Check the attached page image before falling back on this. If the image shows \
the labels, read them and report them as printed_label with normal confidence — \
that is a direct reading, not an inference.

   Only when the labels are unavailable in both the text and the image, assign these \
fields by position and convention: set printed_label to null and lower confidence to \
at most 0.80, because you inferred the field's identity rather than seeing it stated. \
Do not report 1.00 for a value whose meaning you deduced.

   Multiple tax rows. A totals block may contain more than one tax or levy row — \
"State Tax" and "City Tax", or "VAT" and a "Duty" or "Environmental Levy". When it \
does, there will be several amounts between the subtotal and the total, and \
subtotal plus ALL of them equals the total. Report the SUM of every tax/levy row in \
tax_amount, and list each row separately in tax_breakdown with its own label and \
amount. Do not report only the first tax row: a subtotal plus one of two tax rows \
will not reconcile against the total, which is a reading error, not a document \
defect. A single 0.00 tax under a "reverse charge" note is a complete, correct \
tax_amount — report it as 0.00, not as absent.

8. Never return a full bank account number. Report only the last four digits in \
remit_to_account_last4.

9. Amounts are plain decimal strings: strip currency symbols and thousands \
separators, keep the decimal point. "USD 1,234.56" becomes "1234.56". Dates are \
YYYY-MM-DD. Never return a number type for money.

   Credit notes and negative amounts. A document titled "Credit Note" or "Credit \
Memo", or one whose amounts are printed in parentheses or with a leading minus, \
is crediting money back rather than billing for it. Preserve the sign on EVERY \
amount consistently: the line totals, the subtotal, the tax, and the total must \
all carry the same sign. Report "(129.90)" or "-129.90" as "-129.90". Do not \
report a negative subtotal with a positive line total, or vice versa — a credit \
note is internally consistent with negative amounts throughout, and mixing signs \
turns a valid document into an apparent arithmetic error.

10. Exclude subtotal, tax, and total rows from lines. Lines are goods and services \
billed, not summary rows.

11. Report what you could not read in unreadable_regions rather than guessing.

12. Separate a line's quantity from its unit. The Qty column often prints the unit \
beside the number — "10 EA", "2.5 KG", "3 HR". The quantity field takes the numeric \
part only ("10", "2.5", "3"); the unit ("EA", "KG", "HR") goes in unit_of_measure. \
Do not return "10 EA" as the quantity: it is not a number, and the two are separate \
values that happen to be printed in one cell. If no unit is printed, leave \
unit_of_measure null and report the bare number."""


# --- schema anchor: restated every call --------------------------------------

SCHEMA_ANCHOR: Final[str] = """\
Return a single object matching the required schema exactly. Every field reading \
has three parts: value, printed_label, confidence. Do not add fields that are not \
in the schema."""


def build_system_prompt() -> str:
    """Assemble the system prompt.

    A function rather than a constant so the assembly order is explicit and a
    future variant (a tenant-specific addendum, say) has an obvious seam.
    """
    return f"{ROLE}\n\n{OPERATIONAL}\n\n{SCHEMA_ANCHOR}"


def build_user_prompt(
    *,
    markdown: str,
    tables_rendered: str,
    parse_strategy: str,
    has_images: bool,
    escalated_to_ocr: bool,
) -> str:
    """Assemble the per-document prompt.

    Only the slice this component needs is included (FR-15.5): the parsed text, the
    line tables, and a short note about document quality. No policy pack, no ledger
    data, no other agent's context.

    The quality note matters. Telling the model the text came from OCR of a
    degraded scan is what makes rule 4 actionable — otherwise it has no way to know
    that a crisp-looking string was reconstructed from a blurry one.
    """
    quality_note = _quality_note(
        parse_strategy=parse_strategy,
        escalated_to_ocr=escalated_to_ocr,
        has_images=has_images,
    )

    sections = [
        "Extract the invoice data from the document below.",
        quality_note,
        "--- PARSED DOCUMENT TEXT ---",
        markdown.strip() or "(no text recovered)",
    ]

    if tables_rendered.strip():
        sections += [
            "--- DETECTED LINE TABLES ---",
            "Column structure is preserved below. Prefer it over the flat text "
            "above when reading line items, because a quantity is only meaningful "
            "relative to its column.",
            tables_rendered.strip(),
        ]

    if has_images:
        sections.append(
            "--- SOURCE IMAGES ---",
        )
        sections.append(
            "Page and figure images are attached. Use them to confirm values, and "
            "to read anything that appears only inside an image such as a stamped "
            "total or a handwritten annotation."
        )

    return "\n\n".join(sections)


def _quality_note(
    *, parse_strategy: str, escalated_to_ocr: bool, has_images: bool
) -> str:
    if parse_strategy == "ocr":
        note = (
            "Document quality: this text was produced by OCR, so character errors "
            "are likely. Treat digits with particular suspicion and lower your "
            "confidence accordingly."
        )
        if escalated_to_ocr:
            note += (
                " The document had no extractable text layer, indicating a scan "
                "rather than a digital original."
            )
        return note

    note = (
        "Document quality: this text came from a digital text layer, so characters "
        "are exact. Confidence should be high unless a value is genuinely ambiguous "
        "or absent."
    )
    if has_images:
        note += " Some content may additionally appear inside embedded images."
    return note
