"""Cross-encoder reranking of fused candidates (FR-6.4).

Hybrid retrieval (RRF over dense + lexical) is a strong *recall* stage — it
casts a wide net — but its ordering is a fusion of two coarse signals. A
cross-encoder reads the query and each candidate *together* (not as two
independent embeddings) and scores true relevance, which is materially more
accurate for the final ordering. On GL coding the cost of a wrong top result is
a wrong account, so paying ~100ms for a sharper top-k is worth it (the tradeoff
recorded in design §8).

`BAAI/bge-reranker-v2-m3` runs locally via sentence-transformers' `CrossEncoder`,
behind the `ml` extra. The import is lazy so this module loads without torch;
only `rerank()` needs the extra. A `NoopReranker` is provided so the pipeline
runs end to end in the fast test tier and in environments without the ml extra —
it preserves the RRF order, which is a legitimate (if weaker) ranking, rather
than silently pretending reranking happened.
"""

from __future__ import annotations

from typing import Any, Protocol

from ap_agent.config import get_settings
from ap_agent.errors import ConfigurationError, ErrorContext
from ap_agent.rag.retriever import RetrievedChunk


class Reranker(Protocol):
    def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_k: int
    ) -> list[RetrievedChunk]:
        ...


class NoopReranker:
    """Passthrough that keeps RRF order and truncates to top_k.

    Not a silent degradation: RRF order is a real ranking. This exists so the
    fast tier and ml-less environments exercise the full retrieve->rerank->rail
    path without loading a cross-encoder, and so a reranker outage degrades to
    "worse ordering" rather than "no results".
    """

    def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_k: int
    ) -> list[RetrievedChunk]:
        return chunks[:top_k]


class CrossEncoderReranker:
    """`bge-reranker-v2-m3` cross-encoder. Requires the `ml` extra."""

    def __init__(self, *, model_name: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.reranker_model
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:  # pragma: no cover - requires ml extra
                raise ConfigurationError(
                    "Cross-encoder reranking requires the 'ml' extra "
                    "(sentence-transformers, torch). Install it or use "
                    "NoopReranker.",
                    context=ErrorContext(stage="rag.rerank"),
                    cause=exc,
                ) from exc
            self._model = CrossEncoder(self._model_name)
        return self._model

    def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_k: int
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        model = self._get_model()
        scores = model.predict([(query, chunk.content) for chunk in chunks])
        ranked = sorted(zip(chunks, scores, strict=True), key=lambda cs: cs[1], reverse=True)
        return [chunk for chunk, _score in ranked[:top_k]]
