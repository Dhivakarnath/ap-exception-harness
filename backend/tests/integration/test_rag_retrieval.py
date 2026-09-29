"""Hybrid retrieval against real Postgres (`rag.ingest`, `rag.retriever`).

Marked `integration`: needs Postgres with pgvector + the applied migration. Uses
the deterministic `FakeEmbedder` (no torch, no Bedrock) so the SQL-level
guarantees — tenant isolation, effective-date scoping, RRF fusion, the tsvector
lexical path — are tested against the real database engine that enforces them,
without depending on an ML model or the network.

The load-bearing assertions are the two that are *security* properties, not
quality ones:

* cross-tenant retrieval is impossible (FR-6.7), and
* superseded policy can never be cited (FR-6.3).

Both are SQL predicates, so they are tested by trying to breach them and
confirming the database refuses.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest

from ap_agent.persistence.db import session_scope
from ap_agent.persistence.models import PolicyChunk, Tenant
from ap_agent.rag.ingest import ingest_corpus_dir
from ap_agent.rag.rail import apply_rail
from ap_agent.rag.retriever import HybridRetriever
from tests.unit.rag_fakes import FakeEmbedder

pytestmark = pytest.mark.integration

CORPUS_ROOT = Path(__file__).resolve().parents[2] / "rag_corpus"

# The corpus folders are named after the *demo* tenants, but the integration
# suite ingests them under isolated ``-test`` tenant ids and only ever deletes
# those. That way running `make test-int` never wipes the real `retail-demo` /
# `manufacturing-demo` rows a developer keeps in the same Postgres for the live
# demo (`make rag-ingest`). The `-test` suffix is added via `tenant_id_map`.
RETAIL = "retail-demo-test"
MANUFACTURING = "manufacturing-demo-test"
TENANTS = (MANUFACTURING, RETAIL)
AS_OF = date(2026, 2, 27)


def _to_test_tenant(dir_name: str) -> str:
    """Map a corpus directory name to its isolated test tenant id."""
    return f"{dir_name}-test"


@pytest.fixture(scope="module")
def ingested_corpus() -> None:
    """Ingest the real corpus under isolated ``-test`` tenants, then clean up.

    Setup and teardown delete only the ``-test`` tenants, so a developer's live
    demo rows (`retail-demo`, `manufacturing-demo`) are never touched.
    """
    embedder = FakeEmbedder()
    for tenant_id in TENANTS:
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
        s.query(PolicyChunk).filter(PolicyChunk.tenant_id.in_(TENANTS)).delete(
            synchronize_session=False
        )
    with session_scope() as s:
        ingest_corpus_dir(
            s, corpus_root=CORPUS_ROOT, embedder=embedder, tenant_id_map=_to_test_tenant
        )

    yield

    with session_scope() as s:
        s.query(PolicyChunk).filter(PolicyChunk.tenant_id.in_(TENANTS)).delete(
            synchronize_session=False
        )


@pytest.fixture
def retriever() -> HybridRetriever:
    return HybridRetriever(embedder=FakeEmbedder())


class TestIngest:
    def test_corpus_is_ingested_for_both_tenants(self, ingested_corpus: None) -> None:
        with session_scope() as s:
            counts = {
                tenant_id: s.query(PolicyChunk).filter_by(tenant_id=tenant_id).count()
                for tenant_id in TENANTS
            }
        # The corpus is an enterprise-scale, multi-folder set (policy/, precedent/,
        # contracts/) with per-account coding guides, a chart-of-accounts guide, a
        # merchant-category map, capitalization policy, and extensive precedent.
        # Floors, not exact counts, so editing a clause does not break the test;
        # they only guard against a folder silently dropping out of the recursive
        # walk. Retail is much larger than manufacturing by design.
        assert counts[RETAIL] >= 120
        assert counts[MANUFACTURING] >= 40

    def test_reingest_is_idempotent(self, ingested_corpus: None) -> None:
        with session_scope() as s:
            before = s.query(PolicyChunk).filter_by(tenant_id=RETAIL).count()
        with session_scope() as s:
            ingest_corpus_dir(
                s, corpus_root=CORPUS_ROOT, embedder=FakeEmbedder(), tenant_id_map=_to_test_tenant
            )
        with session_scope() as s:
            after = s.query(PolicyChunk).filter_by(tenant_id=RETAIL).count()
        assert after == before  # re-ingest replaced, did not accumulate

    def test_tsvector_is_populated_at_ingest(self, ingested_corpus: None) -> None:
        with session_scope() as s:
            missing = (
                s.query(PolicyChunk)
                .filter(PolicyChunk.tenant_id == RETAIL, PolicyChunk.content_tsv.is_(None))
                .count()
            )
        assert missing == 0

    def test_all_three_doc_types_ingested_from_subfolders(self, ingested_corpus: None) -> None:
        # The recursive walk must pick up every subfolder, and every filename's
        # prefix must resolve to one of the three allowed doc_type values.
        with session_scope() as s:
            doc_types = {
                dt
                for (dt,) in s.query(PolicyChunk.doc_type)
                .filter(PolicyChunk.tenant_id == RETAIL)
                .distinct()
            }
        assert doc_types == {"accounting_policy", "coding_precedent", "vendor_contract"}

    def test_source_names_carry_subfolder_prefix(self, ingested_corpus: None) -> None:
        # source_name is the path relative to the tenant dir, so citation refs are
        # stable and unique even when two subfolders hold similarly named files.
        with session_scope() as s:
            sources = {
                src
                for (src,) in s.query(PolicyChunk.source_name)
                .filter(PolicyChunk.tenant_id == RETAIL)
                .distinct()
            }
        assert any(src.startswith("policy/") for src in sources)
        assert any(src.startswith("precedent/") for src in sources)
        assert any(src.startswith("contracts/") for src in sources)


class TestHybridRetrieval:
    def test_relevant_clause_ranks_for_a_matching_query(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        with session_scope() as s:
            hits = retriever.retrieve(
                s,
                tenant_id=RETAIL,
                query="contract engineering support professional services Initech",
                as_of=AS_OF,
            )
        assert hits
        top_refs = {h.citation_ref for h in hits[:3]}
        # A professional-services source (the 6500 policy clause or the Initech
        # precedent) should be near the top. Asserted as a family rather than one
        # exact ref because the FakeEmbedder has no semantics — lexical coverage
        # decides the order here, and several 6500 sources legitimately compete.
        # Source names are paths relative to the tenant dir now that the corpus is
        # organised into subfolders. A professional-services family is asserted
        # because with the FakeEmbedder the lexical half decides the order and
        # several 6500 sources (the manual clause, the per-account guide, and the
        # Initech precedent in two precedent files) legitimately compete.
        professional_services_refs = {
            "policy/gl_coding_policy_manual.md#1",
            "policy/gl_coding_policy_professional_services.md#4",
            "precedent/coding_precedent_general.md#initech-services-contract-engineering",
            "precedent/coding_precedent_professional_services.md#initech-services-contract-engineering",
        }
        assert top_refs & professional_services_refs

    def test_lexical_half_anchors_an_exact_account_number(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        with session_scope() as s:
            hits = retriever.retrieve(s, tenant_id=RETAIL, query="7200", as_of=AS_OF)
        # The exact token "7200" is a lexical anchor the software clause carries.
        assert any(h.lexical_rank is not None for h in hits)

    def test_results_are_ordered_by_rrf_score(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        with session_scope() as s:
            hits = retriever.retrieve(
                s, tenant_id=RETAIL, query="marketing advertising campaign", as_of=AS_OF
            )
        scores = [h.rrf_score for h in hits]
        assert scores == sorted(scores, reverse=True)


class TestTenantIsolation:
    def test_manufacturing_only_phrase_never_leaks_to_retail(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        # "foundry crucible reline" appears only in the manufacturing corpus.
        with session_scope() as s:
            hits = retriever.retrieve(
                s, tenant_id=RETAIL, query="foundry crucible reline", as_of=AS_OF
            )
        # Whatever weak matches come back, none may be manufacturing chunks.
        assert all(h.tenant_id == RETAIL for h in hits)
        assert all("manufacturing" not in h.content.lower() for h in hits)

    def test_retail_query_against_manufacturing_returns_only_manufacturing(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        with session_scope() as s:
            hits = retriever.retrieve(
                s, tenant_id=MANUFACTURING, query="software subscription SaaS", as_of=AS_OF
            )
        assert all(h.tenant_id == MANUFACTURING for h in hits)


class TestSupersededPolicyExclusion:
    def test_superseded_v1_clause_is_never_retrieved_for_a_current_invoice(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        # The v1 doc deliberately contains the retired account "7000". A 2026
        # invoice must never retrieve it.
        with session_scope() as s:
            hits = retriever.retrieve(
                s, tenant_id=RETAIL, query="software subscription account 7000", as_of=AS_OF
            )
        refs = {h.citation_ref for h in hits}
        assert not any("superseded" in r for r in refs)
        assert all("7000" not in h.content for h in hits)

    def test_a_back_dated_invoice_could_see_the_then_current_policy(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        # As-of a 2025 date, the v1 policy WAS in force — retrieval must scope to
        # the invoice's date, not wall-clock now.
        with session_scope() as s:
            hits = retriever.retrieve(
                s,
                tenant_id=RETAIL,
                query="software subscription account",
                as_of=date(2025, 6, 1),
            )
        # v2 (effective 2026) must NOT appear for a 2025 invoice.
        assert all(h.version != "2.0.0" for h in hits) or not hits


class TestLatencyBudget:
    def test_retrieval_stays_within_a_generous_budget(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        # NFR-1 targets retrieval < 200ms. The FakeEmbedder is near-instant, so
        # this measures the DB round-trips (dense + lexical + fetch), not model
        # latency. A generous ceiling guards against an accidental N+1 or a
        # missing index, not against a tight production SLA.
        with session_scope() as s:
            start = time.perf_counter()
            retriever.retrieve(
                s, tenant_id=RETAIL, query="professional services consulting", as_of=AS_OF
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert elapsed_ms < 500, f"retrieval took {elapsed_ms:.0f}ms"


class TestRailIntegration:
    def test_rail_keeps_relevant_and_reports_drops(
        self, ingested_corpus: None, retriever: HybridRetriever
    ) -> None:
        with session_scope() as s:
            hits = retriever.retrieve(
                s, tenant_id=RETAIL, query="contract engineering support", as_of=AS_OF
            )
        railed = apply_rail(hits)
        assert railed.kept
        assert all(c.tenant_id == RETAIL for c in railed.kept)
