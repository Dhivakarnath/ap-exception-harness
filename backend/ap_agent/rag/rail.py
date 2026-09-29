"""Retrieval rail: drop out-of-scope chunks before they reach the prompt
(FR-6.5).

The metadata pre-filter (in the retriever) already enforces the *hard*
guarantees — tenant isolation and effective-date scoping — as SQL predicates.
The rail is the *soft*, defence-in-depth layer on top: even within the
in-scope, in-force set, a chunk that RRF and the reranker surfaced may still be
too weakly related to belong in the grounding context. Feeding a barely-related
clause to the model invites it to cite something that does not actually support
the code — the exact ungrounded-coding failure the faithfulness gate exists to
catch, moved one step earlier so the model never sees the tempting-but-wrong
chunk in the first place.

Two filters, both conservative:

* **Relevance floor** — drop a chunk whose fused RRF score falls below a
  fraction of the top chunk's score. This removes the long tail that hybrid
  recall pulled in, keeping only chunks in the same relevance league as the
  best match. Relative (a fraction of the top) rather than absolute, because
  RRF scores are not calibrated across queries.

* **Doc-type scope** — an optional allow-list, so a caller that only wants
  accounting policy (not, say, vendor contracts) can say so. GL coding uses
  policy and precedent, which is the default.

The rail returns *what it kept and why it dropped the rest*, so the decision is
inspectable in the UI rather than a silent cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ap_agent.rag.retriever import RetrievedChunk

# A chunk is dropped if its RRF score is below this fraction of the top chunk's.
# 0.30 keeps the same-league matches and cuts the weak tail; deliberately
# lenient, because dropping a genuinely relevant clause (a false negative here)
# is worse than passing a marginal one the faithfulness gate can still reject.
DEFAULT_RELEVANCE_FRACTION = 0.30


# A retrieval result is "weakly grounded" when NO surviving chunk had a lexical
# (full-text) hit — every candidate came from dense similarity alone. Measured
# signature of an out-of-scope query: an invoice whose subject appears nowhere
# in the corpus still returns the nearest dense neighbours (retrieval always
# returns *something*), but shares no actual terms with any clause. Requiring at
# least one lexical anchor to call a result "strongly grounded" is a
# deterministic guard the model cannot rationalise past, complementing the
# prompt's own abstain instruction (defence in depth). See INC-007.


@dataclass(frozen=True, slots=True)
class RailResult:
    """What the rail kept, and an auditable record of what it dropped."""

    kept: list[RetrievedChunk]
    dropped: list[tuple[str, str]] = field(default_factory=list)
    """(citation_ref, reason) for each dropped chunk."""

    @property
    def is_empty(self) -> bool:
        return not self.kept

    @property
    def has_lexical_anchor(self) -> bool:
        """Whether any kept chunk matched on full-text, not dense-only.

        A kept set with no lexical anchor is a weak-grounding signal: the query
        shares no terms with any clause, so a confident code off it is suspect.
        The faithfulness gate uses this to require a human even when the model
        claims high confidence (FR-6.6).
        """
        return any(c.lexical_rank is not None for c in self.kept)


def apply_rail(
    chunks: list[RetrievedChunk],
    *,
    relevance_fraction: float = DEFAULT_RELEVANCE_FRACTION,
    allowed_doc_types: frozenset[str] | None = None,
) -> RailResult:
    """Drop weakly-relevant and out-of-scope chunks, keeping order.

    Empty input yields an empty result rather than raising: "retrieval found
    nothing in scope" is a real, expected answer (it routes GL coding to a
    human), not an error.
    """
    if not chunks:
        return RailResult(kept=[])

    top_score = max(c.rrf_score for c in chunks)
    floor = top_score * relevance_fraction

    kept: list[RetrievedChunk] = []
    dropped: list[tuple[str, str]] = []

    for chunk in chunks:
        if allowed_doc_types is not None and chunk.doc_type not in allowed_doc_types:
            dropped.append((chunk.citation_ref, f"doc_type {chunk.doc_type!r} out of scope"))
            continue
        if chunk.rrf_score < floor:
            dropped.append(
                (
                    chunk.citation_ref,
                    f"relevance {chunk.rrf_score:.4f} below floor {floor:.4f}",
                )
            )
            continue
        kept.append(chunk)

    return RailResult(kept=kept, dropped=dropped)
