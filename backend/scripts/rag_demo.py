"""Demo: ingest the policy corpus and GL-code a non-PO invoice end to end.

    uv run python scripts/rag_demo.py --ingest        # embed + store the corpus
    uv run python scripts/rag_demo.py                 # code a sample non-PO invoice
    uv run python scripts/rag_demo.py --query "trade show booth production"

This is the Slice 7 demo checkpoint: a non-PO invoice becomes a GL code with a
cited policy clause, or routes to a human when the coding cannot be grounded.

Requires Postgres (for `policy_chunks`) and, unless `--fake-embed` is passed,
AWS credentials for Titan embeddings and Nova Lite. `--fake-embed` uses the
deterministic test embedder so the retrieval/rail/gate flow can be demonstrated
without Bedrock — the ranking is weaker but the pipeline is identical.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "backend" / "rag_corpus"

from ap_agent.persistence.db import session_scope  # noqa: E402
from ap_agent.persistence.models import PolicyChunk, Tenant  # noqa: E402
from ap_agent.rag.coding import BedrockCodingModel, propose_gl_coding  # noqa: E402
from ap_agent.rag.ingest import ingest_corpus_dir  # noqa: E402
from ap_agent.rag.rail import apply_rail  # noqa: E402
from ap_agent.rag.retriever import HybridRetriever  # noqa: E402

TENANT = "retail-demo"
MIN_CONFIDENCE = 0.82


def _embedder(fake: bool):  # type: ignore[no-untyped-def]
    if fake:
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        from tests.unit.rag_fakes import FakeEmbedder

        return FakeEmbedder()
    from ap_agent.rag.embeddings import get_embedding_model

    return get_embedding_model()


def ingest(fake: bool) -> None:
    embedder = _embedder(fake)
    for tenant_id in ("manufacturing-demo", "retail-demo"):
        with session_scope() as s:
            if s.get(Tenant, tenant_id) is None:
                s.add(
                    Tenant(
                        tenant_id=tenant_id,
                        name=tenant_id,
                        industry="test",
                        base_currency="USD",
                        active_policy_version="1.0.0",
                    )
                )
    with session_scope() as s:
        s.query(PolicyChunk).filter(
            PolicyChunk.tenant_id.in_(["manufacturing-demo", "retail-demo"])
        ).delete(synchronize_session=False)
    with session_scope() as s:
        results = ingest_corpus_dir(s, corpus_root=CORPUS_ROOT, embedder=embedder)
    print(f"ingested with {embedder.model_id}:")
    for r in results:
        print(f"  {r.tenant_id:20} {r.source_name:36} {r.chunks_written} chunks")


def code(query: str, fake: bool) -> None:
    embedder = _embedder(fake)
    retriever = HybridRetriever(embedder=embedder)

    print("=" * 86)
    print(f"  invoice summary: {query}")
    print("=" * 86)

    with session_scope() as s:
        hits = retriever.retrieve(s, tenant_id=TENANT, query=query, as_of=date(2026, 2, 27))

    railed = apply_rail(hits)
    print(f"  retrieved {len(hits)} candidate(s); rail kept {len(railed.kept)}")
    for chunk in railed.kept[:5]:
        print(
            f"    {chunk.rrf_score:.4f} [{chunk.retrieved_by:7}] {chunk.citation_ref} "
            f":: {chunk.section}"
        )
    for ref, reason in railed.dropped[:3]:
        print(f"    dropped {ref}: {reason}")

    if fake:
        print("\n  (--fake-embed: skipping the live coding model; retrieval demo only)")
        return

    print()
    result = propose_gl_coding(
        BedrockCodingModel(),
        invoice_summary=query,
        rail_result=railed,
        min_confidence=MIN_CONFIDENCE,
    )
    print(f"  outcome    : {result.outcome.value}")
    if result.coding is not None:
        print(f"  gl_account : {result.coding.gl_account}")
        print(f"  confidence : {result.coding.confidence:.2f}")
        print(f"  citations  : {', '.join(result.coding.citations) or '(none)'}")
    print(f"  reasoning  : {result.reasoning}")
    print(f"  auto-code  : {result.auto_applicable}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingest", action="store_true", help="Ingest the corpus and exit.")
    parser.add_argument(
        "--query",
        default="Contract engineering support, monthly retainer from Initech Services",
        help="Invoice summary to code.",
    )
    parser.add_argument(
        "--fake-embed", action="store_true", help="Use the deterministic test embedder."
    )
    args = parser.parse_args(argv)

    if args.ingest:
        ingest(args.fake_embed)
        return 0
    code(args.query, args.fake_embed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
