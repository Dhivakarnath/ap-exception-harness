"""Hybrid retrieval for GL coding (FR-6.2, FR-6.3, FR-6.7).

Three stages, in order:

1. **Metadata pre-filter** (FR-6.3, FR-6.7). A SQL `WHERE` restricts every
   candidate to the querying tenant and to policy in force on the invoice's
   date: `effective_from <= as_of AND (effective_to IS NULL OR effective_to >=
   as_of)`. This is where tenant isolation and the "superseded policy can never
   be cited" guarantee live — both as database predicates, not library
   features, so neither can be bypassed by an application bug.

2. **Parallel dense + lexical retrieval, fused with RRF** (FR-6.2). Dense
   search uses the pgvector HNSW index (cosine); lexical search uses the GIN
   `content_tsv` index (Postgres full-text, the BM25-family ranking). Their two
   ranked lists are merged with Reciprocal Rank Fusion — `score = sum 1/(k +
   rank)` — which is rank-based, so it needs no score normalisation between two
   fundamentally different scoring scales. Dense finds "means the same thing";
   lexical anchors exact identifiers (account numbers, vendor names) that
   embeddings generalise away. Hybrid beats either alone precisely on invoice
   coding, where the query carries both a fuzzy description and hard tokens.

Reranking (FR-6.4) and the retrieval rail (FR-6.5) are separate modules applied
to this retriever's output, kept out of here so each stage is independently
testable.

**As-of date, not wall clock.** The effective-date filter compares against the
invoice's own date, passed in explicitly — never `now()` — so retrieval for a
back-dated invoice sees the policy that was in force *then*, and the result is
reproducible when a run is replayed later. Same determinism discipline as the
policy engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from ap_agent.config import get_settings
from ap_agent.rag.embeddings import EmbeddingModel, get_embedding_model

# Words shorter than this are dropped from the lexical query — they are almost
# all stop words or fragments that add noise, not anchoring power.
_MIN_LEXICAL_TERM_LEN = 3

# RRF smoothing constant. 60 is the value from the original Cormack et al.
# paper and the de-facto default across pgvector/Elasticsearch hybrid
# implementations; it damps the influence of any single ranker's top hit so
# neither dense nor lexical can dominate the fused order on its own.
RRF_K = 60


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One candidate returned by hybrid retrieval, with provenance for citation."""

    chunk_id: str
    tenant_id: str
    doc_type: str
    source_name: str
    section: str | None
    citation_ref: str
    version: str
    content: str
    dense_rank: int | None
    lexical_rank: int | None
    rrf_score: float

    @property
    def retrieved_by(self) -> str:
        """Which ranker(s) surfaced this chunk — shown in the UI for transparency."""
        if self.dense_rank is not None and self.lexical_rank is not None:
            return "hybrid"
        if self.dense_rank is not None:
            return "dense"
        return "lexical"


def _dense_candidates(
    session: Session, tenant_id: str, as_of: date, query_vector: list[float], limit: int
) -> list[tuple[str, int]]:
    """(chunk_id, rank) from pgvector cosine search, in-force policy only."""
    rows = session.execute(
        text(
            """
            SELECT id
            FROM policy_chunks
            WHERE tenant_id = :tenant_id
              AND embedding IS NOT NULL
              AND effective_from <= :as_of
              AND (effective_to IS NULL OR effective_to >= :as_of)
            ORDER BY embedding <=> CAST(:qv AS vector)
            LIMIT :limit
            """
        ),
        {
            "tenant_id": tenant_id,
            "as_of": as_of,
            "qv": str(query_vector),
            "limit": limit,
        },
    ).all()
    return [(row.id, rank) for rank, row in enumerate(rows, start=1)]


def _to_or_tsquery(query: str) -> str:
    """Build an OR-of-terms tsquery string from a natural-language query.

    `plainto_tsquery` ANDs every term, which is the wrong semantics for a
    coding query: "docking stations and external monitors for the office" would
    require a single chunk to contain *all* of dock/station/extern/monitor/offic,
    and no clause does, so lexical retrieval returned nothing even though the
    docking-stations precedent literally contains the phrase (INC-008). Joining
    the significant terms with `|` matches any chunk sharing a term, and
    `ts_rank_cd` still orders by how many terms it covers and how densely — so
    the best-covered clause wins, which is exactly what a lexical anchor should
    reward. The terms are still stemmed by `to_tsquery('english', ...)`, so
    index and query analysis stay consistent.
    """
    # Keep alphanumeric tokens, not just alphabetic: an account number ("7200")
    # or a code ("INV-001") is precisely the kind of exact token lexical search
    # exists to anchor — dropping it would blind the lexical half to the hard
    # identifiers embeddings generalise away. A token qualifies if it is long
    # enough AND contains a letter or is a run of digits (so "7200" stays but a
    # stray "s" does not).
    candidates = re.findall(r"[A-Za-z0-9]+", query)
    terms = [
        t.lower()
        for t in candidates
        if len(t) >= _MIN_LEXICAL_TERM_LEN and (any(c.isalpha() for c in t) or t.isdigit())
    ]
    # Deduplicate while preserving order; a repeated term adds nothing.
    unique = list(dict.fromkeys(terms))
    return " | ".join(unique)


def _lexical_candidates(
    session: Session, tenant_id: str, as_of: date, query: str, limit: int
) -> list[tuple[str, int]]:
    """(chunk_id, rank) from Postgres full-text search, in-force policy only.

    Uses an OR-of-terms `to_tsquery` (see `_to_or_tsquery`) rather than
    `plainto_tsquery`'s all-terms AND, so a multi-word natural-language query
    still finds the clause that shares its key terms. `to_tsquery('english',
    ...)` stems the same way `to_tsvector('english', ...)` did at ingest, so
    index and query analysis remain consistent. `ts_rank_cd` is the
    cover-density ranking — the Postgres full-text analogue of a BM25 score —
    which rewards chunks covering more query terms more densely.
    """
    tsquery = _to_or_tsquery(query)
    if not tsquery:
        return []
    rows = session.execute(
        text(
            """
            SELECT id
            FROM policy_chunks
            WHERE tenant_id = :tenant_id
              AND content_tsv @@ to_tsquery('english', :tsquery)
              AND effective_from <= :as_of
              AND (effective_to IS NULL OR effective_to >= :as_of)
            ORDER BY ts_rank_cd(content_tsv, to_tsquery('english', :tsquery)) DESC
            LIMIT :limit
            """
        ),
        {"tenant_id": tenant_id, "as_of": as_of, "tsquery": tsquery, "limit": limit},
    ).all()
    return [(row.id, rank) for rank, row in enumerate(rows, start=1)]


def _reciprocal_rank_fusion(
    dense: list[tuple[str, int]], lexical: list[tuple[str, int]]
) -> dict[str, tuple[float, int | None, int | None]]:
    """Fuse two ranked lists into {chunk_id: (rrf_score, dense_rank, lexical_rank)}."""
    dense_ranks = dict(dense)
    lexical_ranks = dict(lexical)
    fused: dict[str, tuple[float, int | None, int | None]] = {}
    for chunk_id in set(dense_ranks) | set(lexical_ranks):
        dr = dense_ranks.get(chunk_id)
        lr = lexical_ranks.get(chunk_id)
        score = 0.0
        if dr is not None:
            score += 1.0 / (RRF_K + dr)
        if lr is not None:
            score += 1.0 / (RRF_K + lr)
        fused[chunk_id] = (score, dr, lr)
    return fused


class HybridRetriever:
    """Metadata-filtered, RRF-fused dense+lexical retrieval over `policy_chunks`."""

    def __init__(self, embedder: EmbeddingModel | None = None) -> None:
        self._embedder = embedder or get_embedding_model()

    def retrieve(
        self,
        session: Session,
        *,
        tenant_id: str,
        query: str,
        as_of: date,
        candidates: int | None = None,
    ) -> list[RetrievedChunk]:
        """Return fused candidates for a query, best RRF score first.

        `candidates` bounds each ranker's list before fusion; the caller
        (reranker) narrows to the final top-k. Defaults to
        `settings.retrieval_candidates`.
        """
        settings = get_settings()
        limit = candidates or settings.retrieval_candidates

        query_vector = self._embedder.embed_query(query)
        dense = _dense_candidates(session, tenant_id, as_of, query_vector, limit)
        lexical = _lexical_candidates(session, tenant_id, as_of, query, limit)
        fused = _reciprocal_rank_fusion(dense, lexical)

        if not fused:
            return []

        rows = session.execute(
            text(
                """
                SELECT id, tenant_id, doc_type, source_name, section,
                       citation_ref, version, content
                FROM policy_chunks
                WHERE id = ANY(:ids)
                """
            ),
            {"ids": list(fused.keys())},
        ).all()
        by_id = {row.id: row for row in rows}

        results: list[RetrievedChunk] = []
        for chunk_id, (score, dr, lr) in fused.items():
            row = by_id.get(chunk_id)
            if row is None:  # pragma: no cover - defensive; id came from the same table
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=row.id,
                    tenant_id=row.tenant_id,
                    doc_type=row.doc_type,
                    source_name=row.source_name,
                    section=row.section,
                    citation_ref=row.citation_ref,
                    version=row.version,
                    content=row.content,
                    dense_rank=dr,
                    lexical_rank=lr,
                    rrf_score=score,
                )
            )

        results.sort(key=lambda c: c.rrf_score, reverse=True)
        return results
