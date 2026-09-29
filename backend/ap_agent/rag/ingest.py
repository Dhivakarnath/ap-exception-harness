"""Corpus ingest: markdown files -> embedded, stored `policy_chunks` (FR-6.3).

Pipeline per document: parse front matter -> structure-aware chunk -> embed the
batch -> write rows with the lexical `content_tsv` computed by Postgres. The
dense vector and the lexical vector live in the same row, so there is no second
datastore and no cross-system consistency problem (ADR-008).

**The lexical vector is built by Postgres, not Python.** `content_tsv` is set
with `to_tsvector('english', content)` in the INSERT, so the exact same text
analysis the query side uses (`plainto_tsquery('english', ...)`) is applied at
index time. Computing it in Python with a different tokeniser would make the
BM25/full-text half of hybrid retrieval subtly inconsistent between index and
query — the kind of mismatch that quietly halves recall.

**Re-ingest is idempotent per (tenant, source_name).** Ingesting a corpus file
first deletes that file's existing chunks for the tenant, then re-inserts, so
re-running ingest after editing a policy does not accumulate stale duplicates.
Tenant scoping is a hard SQL predicate on every statement (FR-6.7).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import bindparam, delete, text
from sqlalchemy.orm import Session

from ap_agent.persistence.models import PolicyChunk
from ap_agent.rag.chunking import Chunk, chunk_markdown
from ap_agent.rag.embeddings import EmbeddingModel, get_embedding_model


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one ingest run produced, for logging and tests."""

    tenant_id: str
    source_name: str
    chunks_written: int
    embedding_model: str


def _delete_existing(session: Session, tenant_id: str, source_name: str) -> None:
    session.execute(
        delete(PolicyChunk).where(
            PolicyChunk.tenant_id == tenant_id,
            PolicyChunk.source_name == source_name,
        )
    )


def _insert_chunks(
    session: Session, chunks: list[Chunk], vectors: list[list[float]], model_id: str
) -> None:
    """Insert chunks with the tsvector computed in-database.

    A raw INSERT (rather than ORM objects) is used specifically so
    `content_tsv` can be set with `to_tsvector` in the same statement — the ORM
    column maps to a TSVECTOR but cannot express "compute this from another
    column at insert time".
    """
    stmt = text(
        """
        INSERT INTO policy_chunks (
            id, tenant_id, doc_type, source_name, section, citation_ref,
            version, effective_from, effective_to,
            content, content_tsv, embedding, embedding_model, token_count,
            chunk_metadata, created_at
        ) VALUES (
            gen_random_uuid(), :tenant_id, :doc_type, :source_name, :section,
            :citation_ref, :version, :effective_from, :effective_to,
            :content, to_tsvector('english', :content), :embedding,
            :embedding_model, :token_count, CAST(:chunk_metadata AS JSONB), now()
        )
        """
    ).bindparams(bindparam("embedding"))

    import json

    for chunk, vector in zip(chunks, vectors, strict=True):
        session.execute(
            stmt,
            {
                "tenant_id": chunk.tenant_id,
                "doc_type": chunk.doc_type,
                "source_name": chunk.source_name,
                "section": chunk.section,
                "citation_ref": chunk.citation_ref,
                "version": chunk.version,
                "effective_from": chunk.effective_from,
                "effective_to": chunk.effective_to,
                "content": chunk.content,
                "embedding": str(vector),
                "embedding_model": model_id,
                "token_count": chunk.token_estimate,
                "chunk_metadata": json.dumps(chunk.chunk_metadata),
            },
        )


def ingest_document(
    session: Session,
    *,
    tenant_id: str,
    source_name: str,
    text_content: str,
    embedder: EmbeddingModel | None = None,
) -> IngestResult:
    """Chunk, embed, and store one policy document. Idempotent per source."""
    embedder = embedder or get_embedding_model()
    chunks = chunk_markdown(text_content, tenant_id=tenant_id, source_name=source_name)
    vectors = embedder.embed_documents([c.content for c in chunks])

    _delete_existing(session, tenant_id, source_name)
    _insert_chunks(session, chunks, vectors, embedder.model_id)

    return IngestResult(
        tenant_id=tenant_id,
        source_name=source_name,
        chunks_written=len(chunks),
        embedding_model=embedder.model_id,
    )


def ingest_corpus_dir(
    session: Session,
    *,
    corpus_root: Path,
    embedder: EmbeddingModel | None = None,
    tenant_id_map: Callable[[str], str] | None = None,
) -> list[IngestResult]:
    """Ingest an entire corpus tree laid out as `corpus_root/<tenant>/**/*.md`.

    The top-level directory name *is* the tenant id, which keeps tenant scoping
    explicit at the filesystem level and impossible to get wrong at ingest time.

    The walk is **recursive** (`rglob`), so a tenant's documents can be grouped
    into subfolders (``policy/``, ``precedent/``, ``contracts/``) the way a real
    accounting manual is organised. ``source_name`` is the path *relative to the
    tenant directory* (e.g. ``policy/gl_coding_policy_office_supplies.md``), which
    keeps every ``citation_ref`` unique and stable even when two subfolders hold
    similarly named files. ``doc_type_for`` still resolves on the basename, so the
    filename prefix — not the folder — decides the ``doc_type``.

    ``tenant_id_map`` optionally rewrites the directory name into a different
    stored ``tenant_id`` (e.g. ``retail-demo`` -> ``retail-demo-test``). This lets
    the integration suite ingest the very same corpus under isolated test tenants,
    so its setup/teardown never deletes the real ``retail-demo`` /
    ``manufacturing-demo`` rows a developer keeps for the demo. Default is
    identity: the directory name is the tenant id.
    """
    embedder = embedder or get_embedding_model()
    results: list[IngestResult] = []
    for tenant_dir in sorted(p for p in corpus_root.iterdir() if p.is_dir()):
        tenant_id = tenant_dir.name if tenant_id_map is None else tenant_id_map(tenant_dir.name)
        for md_file in sorted(tenant_dir.rglob("*.md")):
            source_name = md_file.relative_to(tenant_dir).as_posix()
            results.append(
                ingest_document(
                    session,
                    tenant_id=tenant_id,
                    source_name=source_name,
                    text_content=md_file.read_text(encoding="utf-8"),
                    embedder=embedder,
                )
            )
    return results
