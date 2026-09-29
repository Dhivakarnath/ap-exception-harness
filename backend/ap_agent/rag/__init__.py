"""Hybrid RAG for GL coding of non-PO invoices (Slice 7).

The one place a model reasons rather than reads: a non-PO invoice inherits no
GL account from a purchase order, so accounting treatment must be determined
from the tenant's own policy and coded precedent, with a citation, or escalated
to a human. This package is the retrieve -> rerank -> rail -> propose -> gate
pipeline that does that.

Load-bearing guarantees:

* **Tenant isolation and effective-date scoping are SQL predicates** in the
  retriever, not library features (FR-6.3, FR-6.7). Cross-tenant retrieval and
  citing superseded policy are impossible, not merely discouraged.
* **Auto-coding without a citation is structurally impossible** (FR-5.4). The
  faithfulness gate verifies every citation against the actually-retrieved set;
  a fabricated citation cannot make a code auto-applicable.
* **Learn semantically, enforce deterministically** (ADR-009): embeddings and
  reranking *propose* which clauses are relevant, but the gate's auto-code-vs-
  HITL decision is a pure, replayable function of the proposal and the context.
"""

from ap_agent.rag.coding import (
    FaithfulnessOutcome,
    GLCodingResult,
    RawGLProposal,
    evaluate_faithfulness,
    propose_gl_coding,
)
from ap_agent.rag.embeddings import get_embedding_model
from ap_agent.rag.ingest import ingest_corpus_dir, ingest_document
from ap_agent.rag.rail import RailResult, apply_rail
from ap_agent.rag.rerank import CrossEncoderReranker, NoopReranker
from ap_agent.rag.retriever import HybridRetriever, RetrievedChunk

__all__ = [
    "CrossEncoderReranker",
    "FaithfulnessOutcome",
    "GLCodingResult",
    "HybridRetriever",
    "NoopReranker",
    "RailResult",
    "RawGLProposal",
    "RetrievedChunk",
    "apply_rail",
    "evaluate_faithfulness",
    "get_embedding_model",
    "ingest_corpus_dir",
    "ingest_document",
    "propose_gl_coding",
]
