-- Runs once on first container start.
-- pgvector is a hard requirement, not optional (ADR-008).
CREATE EXTENSION IF NOT EXISTS vector;

-- Trigram support for fuzzy vendor / invoice-number matching used by
-- duplicate detection and vendor entity resolution (FR-4.3, FR-3.3).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
