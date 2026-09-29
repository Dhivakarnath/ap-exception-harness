"""GL coding proposal + faithfulness gate (FR-5.2, FR-5.3, FR-5.4, FR-6.6).

The one place a model is asked to *reason*, not just read: given a non-PO
invoice and a set of retrieved, in-force policy/precedent chunks, propose the GL
account (and cost center / entity) and cite the clause that justifies it. This
is the RAG payload — everything upstream (chunk, embed, retrieve, rerank, rail)
exists to put the right few clauses in front of this step.

Two structural guarantees, both enforced in harness code, not by asking the
model nicely (FR-15.7):

1. **Auto-coding without a citation is structurally impossible** (FR-5.4). The
   proposal's `citations` must reference chunks that were actually retrieved for
   this invoice — a citation the model invented, or one pointing at a chunk it
   was not given, is rejected. The canonical `GLCoding.is_groundable` is the
   final assertion; this module is what makes it true or fails loud.

2. **The faithfulness gate** (FR-6.6) decides `AUTO_CODE` vs `HITL`: a proposal
   auto-applies only if it is grounded (cites a real retrieved chunk) *and*
   clears the tenant's `min_confidence_for_gl_coding` threshold. Uncited or
   low-confidence -> HITL, always. There is no branch in which an ungrounded
   code is applied.

The model returns a schema-constrained object (`with_structured_output`); its
`citation_refs` are validated against the retrieved set before anything is
trusted. A hallucinated account with a fabricated citation fails the grounding
check and routes to a human — the correct, safe outcome — rather than posting a
wrong code.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ap_agent.core.canonical import GLCoding
from ap_agent.llm.usage import unpack_structured, zero_usage
from ap_agent.rag.rail import RailResult
from ap_agent.rag.retriever import RetrievedChunk


class FaithfulnessOutcome(StrEnum):
    """What the gate concluded about a proposed coding."""

    AUTO_CODE = "auto_code"
    """Grounded in a real retrieved chunk and above the confidence threshold."""

    HITL_UNGROUNDED = "hitl_ungrounded"
    """No valid citation to a retrieved chunk. Never auto-applied."""

    HITL_LOW_CONFIDENCE = "hitl_low_confidence"
    """Cited, but below the tenant's GL-coding confidence threshold."""

    HITL_WEAK_GROUNDING = "hitl_weak_grounding"
    """Cited and confident, but the grounding set had no lexical anchor — the
    query shares no terms with any clause, so retrieval returned only loose
    dense neighbours. A deterministic guard against a model over-confidently
    coding an out-of-scope invoice (INC-007)."""

    HITL_NO_CONTEXT = "hitl_no_context"
    """Retrieval/rail returned nothing to ground against; a human must code."""


class RawGLProposal(BaseModel):
    """What the model returns for a coding request. Schema-constrained.

    `extra="forbid"` so an invented field is a validation error, and
    `citation_refs` are strings the model must copy from the supplied chunks —
    the grounding check then verifies they were actually in the retrieved set.
    """

    model_config = ConfigDict(extra="forbid")

    gl_account: str = Field(
        default="",
        description=(
            "The proposed GL account number, e.g. '6500'. Use an empty string "
            "when no account in the chart applies to this invoice — never null."
        ),
    )

    @field_validator("gl_account", mode="before")
    @classmethod
    def _null_account_is_empty(cls, v: object) -> object:
        # A model that wants to say "no account applies" tends to return null
        # despite the schema asking for a string. Treat null as the empty
        # string (which the faithfulness gate reads as "no code proposed" and
        # routes to a human) rather than crashing structured-output validation.
        return "" if v is None else v
    cost_center: str | None = None
    entity: str | None = None
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How well the cited clause supports this exact account for this "
            "invoice. Lower it when the match is by analogy rather than a direct "
            "clause, or when two categories plausibly apply."
        ),
    )
    citation_refs: tuple[str, ...] = Field(
        default=(),
        description=(
            "The citation_ref value(s) of the retrieved chunk(s) that justify "
            "this account. Copy them exactly from the supplied context. If none "
            "of the supplied clauses justify a code, return an empty list rather "
            "than inventing a citation."
        ),
    )
    reasoning: str | None = None


class GLCodingResult(BaseModel):
    """The outcome of a coding attempt: the proposal, the gate verdict, and why."""

    model_config = ConfigDict(frozen=True)

    coding: GLCoding | None
    """The validated coding. None when there was no context to ground against."""
    outcome: FaithfulnessOutcome
    reasoning: str
    grounded_citations: tuple[str, ...] = ()
    """Citations that were verified against the retrieved set (subset of the
    model's proposed refs)."""
    retrieval_context: tuple[str, ...] = ()
    """Chunk text passed to the model after the retrieval rail (for eval/RAG triad)."""
    input_tokens: int = 0
    output_tokens: int = 0
    """Token usage of the coding model call (0 when the rail was empty and the
    model was never called, or for a scripted stub). Surfaced so the run budget
    and the cost event can attribute the coding step's real cost (FR-12.5)."""

    @property
    def auto_applicable(self) -> bool:
        return self.outcome is FaithfulnessOutcome.AUTO_CODE


def _verify_citations(
    proposed: tuple[str, ...], retrieved: list[RetrievedChunk]
) -> tuple[str, ...]:
    """Keep only proposed citations that point at an actually-retrieved chunk.

    This is the structural anti-hallucination check: a citation the model made
    up, or one referencing a chunk it was never shown, is silently dropped here
    and therefore cannot make the coding groundable. The model cannot cite its
    way to auto-application with a fabricated ref.
    """
    valid_refs = {chunk.citation_ref for chunk in retrieved}
    return tuple(ref for ref in proposed if ref in valid_refs)


def evaluate_faithfulness(
    proposal: RawGLProposal,
    rail_result: RailResult,
    *,
    min_confidence: float,
) -> GLCodingResult:
    """Apply the faithfulness gate to a model proposal (FR-5.4, FR-6.6).

    Pure function of the proposal, the surviving context, and the threshold — no
    model call, no I/O — so the auto-code-vs-HITL decision is deterministic and
    replayable.
    """
    if rail_result.is_empty:
        return GLCodingResult(
            coding=None,
            outcome=FaithfulnessOutcome.HITL_NO_CONTEXT,
            reasoning=(
                "Retrieval returned no in-scope policy or precedent to ground a "
                "code against. Routing to a human for coding."
            ),
        )

    if not proposal.gl_account.strip():
        # The model declined to propose an account (out of scope for the chart).
        # Route to a human regardless of any citations it may have attached.
        return GLCodingResult(
            coding=None,
            outcome=FaithfulnessOutcome.HITL_UNGROUNDED,
            reasoning=(
                "The model proposed no GL account for this invoice — no account "
                "in the chart applies. Routing to a human."
            ),
        )

    grounded = _verify_citations(proposal.citation_refs, rail_result.kept)

    if not grounded:
        # Either the model cited nothing, or every citation it gave was
        # fabricated / not in the retrieved set. Both are ungrounded.
        return GLCodingResult(
            coding=None,
            outcome=FaithfulnessOutcome.HITL_UNGROUNDED,
            reasoning=(
                f"Proposed account {proposal.gl_account!r} is not grounded in any "
                "retrieved clause (no valid citation). Auto-coding without a "
                "citation is not permitted; routing to a human."
            ),
        )

    coding = GLCoding(
        gl_account=proposal.gl_account,
        cost_center=proposal.cost_center,
        entity=proposal.entity,
        confidence=proposal.confidence,
        citations=grounded,
        reasoning=proposal.reasoning,
        inherited_from_po=False,
    )

    if not rail_result.has_lexical_anchor:
        # Cited and possibly confident, but every retrieved chunk came from
        # dense similarity alone — the invoice shares no terms with any clause.
        # This is the out-of-scope signature (INC-007): retrieval always returns
        # its nearest neighbours, so a confident-looking code off a lexically
        # unanchored set is exactly the over-grounding a human must catch.
        return GLCodingResult(
            coding=coding,
            outcome=FaithfulnessOutcome.HITL_WEAK_GROUNDING,
            reasoning=(
                f"Account {proposal.gl_account!r} was cited, but no retrieved "
                "clause shared any terms with the invoice (dense-only match). "
                "Grounding is too weak to auto-code; routing to a human."
            ),
            grounded_citations=grounded,
        )

    if proposal.confidence < min_confidence:
        return GLCodingResult(
            coding=coding,
            outcome=FaithfulnessOutcome.HITL_LOW_CONFIDENCE,
            reasoning=(
                f"Account {proposal.gl_account!r} is cited but confidence "
                f"{proposal.confidence:.2f} is below the {min_confidence:.2f} "
                "threshold for auto-coding. Routing to a human."
            ),
            grounded_citations=grounded,
        )

    return GLCodingResult(
        coding=coding,
        outcome=FaithfulnessOutcome.AUTO_CODE,
        reasoning=(
            f"Account {proposal.gl_account!r} grounded in {', '.join(grounded)} "
            f"at confidence {proposal.confidence:.2f}."
        ),
        grounded_citations=grounded,
    )


# --- context assembly + model call ------------------------------------------


def build_coding_context(chunks: list[RetrievedChunk]) -> str:
    """Render retrieved chunks as the grounding block for the coding prompt.

    Each chunk is labelled with its exact `citation_ref` so the model can copy
    the ref verbatim into `citation_refs` — the grounding check then matches on
    that exact string.
    """
    if not chunks:
        return "(no policy or precedent retrieved)"
    blocks = [
        f"[{chunk.citation_ref}] ({chunk.doc_type}, v{chunk.version})\n{chunk.content}"
        for chunk in chunks
    ]
    return "\n\n---\n\n".join(blocks)


class CodingModel(Protocol):
    """Minimal model surface the coding node needs.

    A scripted stub satisfies this in the fast test tier, so the grounding and
    faithfulness logic is fully exercised without live credentials — the same
    pattern as the extractor's `ModelClient`.

    Returns the proposal **and** the call's token usage (`{"input_tokens",
    "output_tokens", "total_tokens"}`), so a run's cost can be attributed to the
    coding step. A scripted stub returns zeros (no tokens were spent).
    """

    def propose_coding(
        self, *, system_prompt: str, user_prompt: str, schema: type
    ) -> tuple[RawGLProposal, dict[str, int]]:
        ...


class ScriptedCodingModel:
    """Test double returning a pre-built proposal (and, optionally, a usage dict).

    ``usage`` lets a test assert cost attribution deterministically without a
    live model: pass e.g. ``{"input_tokens": 800, "output_tokens": 120}`` to
    stand in for what a real call would have reported. Defaults to zeros.
    """

    def __init__(
        self, proposal: RawGLProposal, *, usage: dict[str, int] | None = None
    ) -> None:
        self._proposal = proposal
        self._usage = usage or zero_usage()
        self.calls: list[dict[str, str]] = []

    def propose_coding(
        self, *, system_prompt: str, user_prompt: str, schema: type
    ) -> tuple[RawGLProposal, dict[str, int]]:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return self._proposal, dict(self._usage)


class BedrockCodingModel:
    """Live Nova Lite coding model via `with_structured_output`.

    Reuses the same `ChatBedrockConverse` configuration as extraction
    (temperature 0, structured output). Kept here rather than in `extract`
    because the schema and prompt are coding-specific.
    """

    def __init__(self, *, model_id: str | None = None, region: str | None = None) -> None:
        from ap_agent.config import get_settings

        settings = get_settings()
        self._model_id = model_id or settings.bedrock_model_id
        self._region = region or settings.aws_region
        self._llm: object | None = None

    def _get_llm(self) -> object:
        if self._llm is None:
            from langchain_aws import ChatBedrockConverse

            self._llm = ChatBedrockConverse(
                model_id=self._model_id, region_name=self._region, temperature=0, max_tokens=1024
            )
        return self._llm

    def propose_coding(
        self, *, system_prompt: str, user_prompt: str, schema: type
    ) -> tuple[RawGLProposal, dict[str, int]]:
        # include_raw=True so the underlying AIMessage (and its usage_metadata)
        # comes back alongside the parsed object — without it token counts are
        # discarded and the run's cost cannot be reported (INC-015).
        structured = self._get_llm().with_structured_output(  # type: ignore[attr-defined]
            schema, include_raw=True
        )
        from ap_agent.llm.invoke_context import langchain_invoke_config

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        invoke_config = langchain_invoke_config()
        result = (
            structured.invoke(messages, config=invoke_config)
            if invoke_config
            else structured.invoke(messages)
        )
        parsed, usage = unpack_structured(result)
        if not isinstance(parsed, RawGLProposal):  # pragma: no cover - schema-constrained
            raise TypeError(f"Expected RawGLProposal, got {type(parsed).__name__}")
        return parsed, usage


def propose_gl_coding(
    model: CodingModel,
    *,
    invoice_summary: str,
    rail_result: RailResult,
    min_confidence: float,
) -> GLCodingResult:
    """End-to-end: build context, ask the model, apply the faithfulness gate.

    Takes an already-railed `RailResult` (the retrieve->rerank->rail stages run
    upstream) so this function stays a thin, testable seam between the model and
    the gate. When the rail returned nothing, the model is not even called — a
    human codes it — because there is nothing to ground against.
    """
    from ap_agent.agent.prompts.gl_coding import build_system_prompt, build_user_prompt

    if rail_result.is_empty:
        return evaluate_faithfulness(
            RawGLProposal(gl_account="", confidence=0.0), rail_result, min_confidence=min_confidence
        )

    context_block = build_coding_context(rail_result.kept)
    proposal, usage = model.propose_coding(
        system_prompt=build_system_prompt(),
        user_prompt=build_user_prompt(
            invoice_summary=invoice_summary, context_block=context_block
        ),
        schema=RawGLProposal,
    )
    result = evaluate_faithfulness(proposal, rail_result, min_confidence=min_confidence)
    context_texts = tuple(c.content for c in rail_result.kept)
    # The gate is a pure function of the proposal; usage is a property of the
    # call that produced it, attached here so the budget can account for it.
    return result.model_copy(
        update={
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "retrieval_context": context_texts,
        }
    )
