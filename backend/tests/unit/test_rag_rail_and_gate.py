"""Retrieval rail (`rag.rail`) and faithfulness gate (`rag.coding`).

Pure-logic tier: no DB, no model. The gate is the structural guarantee that an
uncited or low-confidence code can never auto-apply (FR-5.4, FR-6.6), so its
branches are pinned exhaustively.
"""

from __future__ import annotations

from ap_agent.rag.coding import (
    FaithfulnessOutcome,
    RawGLProposal,
    ScriptedCodingModel,
    evaluate_faithfulness,
    propose_gl_coding,
)
from ap_agent.rag.rail import RailResult, apply_rail
from tests.unit.rag_fakes import make_chunk


class TestRail:
    def test_empty_input_yields_empty_result(self) -> None:
        result = apply_rail([])
        assert result.is_empty
        assert result.dropped == []

    def test_keeps_chunks_in_the_top_relevance_league(self) -> None:
        chunks = [
            make_chunk(citation_ref="p.md#1", rrf_score=0.05),
            make_chunk(citation_ref="p.md#2", rrf_score=0.04),
        ]
        result = apply_rail(chunks)
        assert len(result.kept) == 2
        assert not result.dropped

    def test_drops_weak_tail_below_relevance_floor(self) -> None:
        chunks = [
            make_chunk(citation_ref="p.md#1", rrf_score=0.05),
            make_chunk(citation_ref="p.md#weak", rrf_score=0.001),  # far below 30% of top
        ]
        result = apply_rail(chunks)
        kept_refs = {c.citation_ref for c in result.kept}
        assert kept_refs == {"p.md#1"}
        assert any(ref == "p.md#weak" for ref, _reason in result.dropped)

    def test_doc_type_allow_list_filters_out_of_scope(self) -> None:
        chunks = [
            make_chunk(citation_ref="p.md#1", rrf_score=0.05, doc_type="accounting_policy"),
            make_chunk(citation_ref="c.md#1", rrf_score=0.05, doc_type="vendor_contract"),
        ]
        result = apply_rail(chunks, allowed_doc_types=frozenset({"accounting_policy"}))
        assert {c.citation_ref for c in result.kept} == {"p.md#1"}
        assert any(ref == "c.md#1" for ref, _ in result.dropped)

    def test_has_lexical_anchor_true_when_any_kept_chunk_matched_lexically(self) -> None:
        result = apply_rail([make_chunk(citation_ref="p.md#1", lexical_rank=1)])
        assert result.has_lexical_anchor is True

    def test_has_lexical_anchor_false_when_all_dense_only(self) -> None:
        result = apply_rail(
            [make_chunk(citation_ref="p.md#1", dense_rank=1, lexical_rank=None)]
        )
        assert result.has_lexical_anchor is False


class TestFaithfulnessGate:
    def _rail(self, *refs: str) -> RailResult:
        return RailResult(kept=[make_chunk(citation_ref=r) for r in refs])

    def test_no_context_routes_to_human(self) -> None:
        result = evaluate_faithfulness(
            RawGLProposal(gl_account="6500", confidence=0.9), RailResult(kept=[]),
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.HITL_NO_CONTEXT
        assert result.coding is None

    def test_grounded_and_confident_auto_codes(self) -> None:
        result = evaluate_faithfulness(
            RawGLProposal(
                gl_account="6500", confidence=0.95, citation_refs=("p.md#1",)
            ),
            self._rail("p.md#1"),
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.AUTO_CODE
        assert result.coding is not None
        assert result.coding.citations == ("p.md#1",)
        assert result.coding.is_groundable
        assert result.auto_applicable

    def test_null_account_is_coerced_to_empty_not_a_crash(self) -> None:
        # A model that wants to say "no account applies" returns null; the
        # schema must not crash on it (INC-008).
        proposal = RawGLProposal.model_validate(
            {"gl_account": None, "confidence": 0.2, "citation_refs": []}
        )
        assert proposal.gl_account == ""

    def test_empty_account_routes_to_human(self) -> None:
        result = evaluate_faithfulness(
            RawGLProposal(gl_account="", confidence=0.2),
            self._rail("p.md#1"),
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.HITL_UNGROUNDED
        assert result.coding is None

    def test_uncited_proposal_routes_to_human(self) -> None:
        result = evaluate_faithfulness(
            RawGLProposal(gl_account="6500", confidence=0.99, citation_refs=()),
            self._rail("p.md#1"),
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.HITL_UNGROUNDED
        assert result.coding is None

    def test_fabricated_citation_is_rejected(self) -> None:
        # The model cited a ref that was never retrieved — cannot ground on it.
        result = evaluate_faithfulness(
            RawGLProposal(
                gl_account="6500", confidence=0.99, citation_refs=("made-up.md#9",)
            ),
            self._rail("p.md#1"),
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.HITL_UNGROUNDED
        assert result.coding is None

    def test_partially_valid_citations_keep_only_the_real_ones(self) -> None:
        result = evaluate_faithfulness(
            RawGLProposal(
                gl_account="6500",
                confidence=0.95,
                citation_refs=("p.md#1", "fabricated.md#2"),
            ),
            self._rail("p.md#1"),
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.AUTO_CODE
        assert result.grounded_citations == ("p.md#1",)

    def test_dense_only_grounding_routes_to_human_even_when_confident(self) -> None:
        # No kept chunk has a lexical rank -> the query shares no terms with any
        # clause. Confident + cited, but weak grounding (INC-007).
        rail = RailResult(
            kept=[make_chunk(citation_ref="p.md#1", dense_rank=1, lexical_rank=None)]
        )
        result = evaluate_faithfulness(
            RawGLProposal(gl_account="6500", confidence=0.99, citation_refs=("p.md#1",)),
            rail,
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.HITL_WEAK_GROUNDING
        assert not result.auto_applicable

    def test_lexical_anchor_permits_auto_code(self) -> None:
        # At least one kept chunk matched on full-text -> grounding is anchored.
        rail = RailResult(
            kept=[make_chunk(citation_ref="p.md#1", dense_rank=1, lexical_rank=2)]
        )
        result = evaluate_faithfulness(
            RawGLProposal(gl_account="6500", confidence=0.95, citation_refs=("p.md#1",)),
            rail,
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.AUTO_CODE

    def test_low_confidence_routes_to_human_even_when_cited(self) -> None:
        result = evaluate_faithfulness(
            RawGLProposal(
                gl_account="6500", confidence=0.50, citation_refs=("p.md#1",)
            ),
            self._rail("p.md#1"),
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.HITL_LOW_CONFIDENCE
        # Coding is still returned (cited), but marked not auto-applicable.
        assert result.coding is not None
        assert not result.auto_applicable

    def test_confidence_exactly_at_threshold_auto_codes(self) -> None:
        result = evaluate_faithfulness(
            RawGLProposal(
                gl_account="6500", confidence=0.82, citation_refs=("p.md#1",)
            ),
            self._rail("p.md#1"),
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.AUTO_CODE


class TestProposeGlCodingOrchestration:
    def test_empty_rail_never_calls_the_model(self) -> None:
        model = ScriptedCodingModel(RawGLProposal(gl_account="6500", confidence=0.9))
        result = propose_gl_coding(
            model, invoice_summary="x", rail_result=RailResult(kept=[]), min_confidence=0.82
        )
        assert result.outcome is FaithfulnessOutcome.HITL_NO_CONTEXT
        assert model.calls == []  # the model was not consulted

    def test_grounded_proposal_flows_through_to_auto_code(self) -> None:
        model = ScriptedCodingModel(
            RawGLProposal(gl_account="6500", confidence=0.95, citation_refs=("p.md#1",))
        )
        rail = RailResult(kept=[make_chunk(citation_ref="p.md#1")])
        result = propose_gl_coding(
            model, invoice_summary="Initech contract engineering", rail_result=rail,
            min_confidence=0.82,
        )
        assert result.outcome is FaithfulnessOutcome.AUTO_CODE
        assert len(model.calls) == 1
        # The context block with the citable ref reached the model.
        assert "p.md#1" in model.calls[0]["user_prompt"]
