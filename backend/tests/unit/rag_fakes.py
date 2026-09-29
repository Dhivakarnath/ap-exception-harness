"""Deterministic test doubles for the RAG pipeline.

A hash-based `FakeEmbedder` produces stable, normalised 1024-dim vectors from
text without torch or Bedrock, so retrieval/RRF/rail/gate logic is fully
exercisable in the fast and integration tiers. Its vectors carry no real
semantics — the *lexical* half of hybrid retrieval does the meaningful ranking
in these tests — but they are reproducible, which is what the logic tests need.
"""

from __future__ import annotations

import hashlib
import math

from ap_agent.rag.retriever import RetrievedChunk


class FakeEmbedder:
    """Deterministic, normalised pseudo-embeddings. No ML deps."""

    model_id = "fake:test"
    dimensions = 1024

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.lower().encode()).digest()
        raw = [(digest[i % len(digest)] - 128) / 128.0 for i in range(self.dimensions)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def make_chunk(
    *,
    citation_ref: str,
    content: str = "clause text",
    rrf_score: float = 0.05,
    doc_type: str = "accounting_policy",
    dense_rank: int | None = 1,
    lexical_rank: int | None = 1,
    version: str = "2.0.0",
) -> RetrievedChunk:
    """Build a `RetrievedChunk` directly, for rail/gate tests that do not need a DB."""
    return RetrievedChunk(
        chunk_id=f"id-{citation_ref}",
        tenant_id="retail-demo",
        doc_type=doc_type,
        source_name=citation_ref.split("#", 1)[0],
        section=citation_ref,
        citation_ref=citation_ref,
        version=version,
        content=content,
        dense_rank=dense_rank,
        lexical_rank=lexical_rank,
        rrf_score=rrf_score,
    )
