# ADR-008: pgvector in existing Postgres

## Status
Accepted

## Context
Hybrid RAG needs dense vectors and BM25 in one transactional store with tenant isolation as SQL predicates.

## Decision
Use pgvector in the same Postgres instance as invoices, audit trail, and HITL reviews. No dedicated vector DB in v1.

## Consequences
- Single dependency, no dual-write sync
- Revisit above ~10M vectors
