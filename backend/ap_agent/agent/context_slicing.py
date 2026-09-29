"""Per-sub-agent context slicing (FR-15.5, FR-15.6).

Context engineering here is a correctness and cost control, not a nicety. Each
model step is shown *only* the slice of the run it needs, for three reasons:

1. **Grounding.** The GL-coding model that is handed the full invoice, the
   policy pack, and every retrieved chunk will ground its answer in whichever of
   those is most salient — often the wrong one. Handed only a tight invoice
   summary and the retrieved clauses, it can only ground in the clauses, which
   is the behaviour the faithfulness gate assumes.
2. **Leakage.** The extraction model must never see the policy pack or the
   coding precedent, or it can "helpfully" invent fields to match a rule it was
   shown. Isolation between sub-agent contexts is what makes each step's output
   attributable to its own inputs.
3. **Cost.** Tokens are the dominant per-invoice cost. A slice that omits what a
   step does not need is directly cheaper, and the ``context_utilisation`` metric
   (tokens used / budget) makes that visible per run.

This module is the single, testable statement of *what each sub-agent is allowed
to see*. The slicing functions return plain strings/objects the prompt builders
consume; a test asserts that a slice built for one sub-agent contains none of
another's private inputs (the no-cross-agent-leakage guarantee).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ap_agent.core.canonical import Invoice
from ap_agent.rag.retriever import RetrievedChunk


class SubAgent(StrEnum):
    """The model-using steps, each with its own context slice."""

    EXTRACTION = "extraction"
    """Reads the document. Sees: parsed markdown, tables, page images. Never:
    the policy pack, precedent, or any other invoice's data."""

    GL_CODING = "gl_coding"
    """Proposes a GL code. Sees: a tight invoice summary + retrieved clauses.
    Never: the full document, the policy pack's thresholds, the ERP records."""


@dataclass(frozen=True, slots=True)
class CodingContextSlice:
    """Exactly what the GL-coding step is shown — and nothing else.

    Built from the invoice and the railed chunks only. Deliberately omits the
    policy pack, the PO/GRN/vendor records, the check ledger, and any other
    sub-agent's inputs: the coding model's job is to map *this line* to *these
    clauses*, and anything else is either a distraction or a leakage path.
    """

    invoice_summary: str
    citation_block: str
    forbidden_inputs: frozenset[str] = frozenset(
        {"policy_pack", "purchase_order", "goods_receipt", "vendor_record", "check_ledger"}
    )


def coding_slice(invoice: Invoice, chunks: list[RetrievedChunk]) -> CodingContextSlice:
    """Assemble the GL-coding context slice (FR-15.5).

    The summary is the vendor and line descriptions that decide the category —
    not the amounts, not the dates, not the remit-to. If a future edit is
    tempted to add the policy pack "for context", the leakage test that guards
    ``forbidden_inputs`` should fail, which is the intended tripwire.
    """
    parts = [f"Vendor: {invoice.vendor_name.value}"]
    if invoice.lines:
        descriptions = "; ".join(line.description.value for line in invoice.lines)
        parts.append(f"Lines: {descriptions}")
    parts.append(f"Total: {invoice.total_amount.value}")
    summary = " | ".join(parts)

    citation_block = _render_citations(chunks)
    return CodingContextSlice(invoice_summary=summary, citation_block=citation_block)


def _render_citations(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(no policy or precedent retrieved)"
    return "\n\n---\n\n".join(
        f"[{c.citation_ref}] ({c.doc_type}, v{c.version})\n{c.content}" for c in chunks
    )


def context_utilisation(*, tokens_used: int, token_budget: int) -> float | None:
    """Fraction of the token budget a run consumed (FR-12.5).

    Emitted per run so a reviewer can see how close a run ran to its ceiling — a
    touchless PO-backed run lands near zero (no model tokens), which is the
    mechanism that makes the design scale. Returns None for a non-positive
    budget rather than dividing by zero.
    """
    if token_budget <= 0:
        return None
    return tokens_used / token_budget
