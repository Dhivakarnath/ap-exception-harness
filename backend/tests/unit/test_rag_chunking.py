"""Structure-aware chunking (`rag.chunking`)."""

from __future__ import annotations

from datetime import date

import pytest

from ap_agent.errors import RetrievalError
from ap_agent.rag.chunking import (
    MAX_CHUNK_CHARS,
    chunk_markdown,
    doc_type_for,
    parse_front_matter,
)

_DOC = """# Retail — GL Coding Policy

Version: 2.0.0
Effective from: 2026-01-01

## 1. Professional Services

Account 6500. Consulting and contract labour code here.

## 2. Software Subscriptions

Account 7200. Recurring SaaS fees code here.
"""


class TestDocType:
    def test_policy_stem_maps_to_accounting_policy(self) -> None:
        assert doc_type_for("gl_coding_policy.md") == "accounting_policy"

    def test_superseded_variant_keeps_base_type(self) -> None:
        assert doc_type_for("gl_coding_policy_v1_superseded.md") == "accounting_policy"

    def test_precedent_stem_maps_to_coding_precedent(self) -> None:
        assert doc_type_for("coding_precedent.md") == "coding_precedent"

    def test_unknown_stem_fails_loud(self) -> None:
        with pytest.raises(RetrievalError, match="doc_type"):
            doc_type_for("random_notes.md")


class TestFrontMatter:
    def test_parses_version_and_dates(self) -> None:
        meta = parse_front_matter(_DOC, "gl_coding_policy.md")
        assert meta.version == "2.0.0"
        assert meta.effective_from == date(2026, 1, 1)
        assert meta.effective_to is None

    def test_parses_effective_to_when_present(self) -> None:
        text = _DOC.replace("Effective from: 2026-01-01", "Effective from: 2025-01-01\nEffective to: 2025-12-31")
        meta = parse_front_matter(text, "old.md")
        assert meta.effective_to == date(2025, 12, 31)

    def test_missing_front_matter_fails_loud(self) -> None:
        with pytest.raises(RetrievalError, match="front matter"):
            parse_front_matter("## 1. A section\n\nbody", "broken.md")


class TestChunkMarkdown:
    def test_one_chunk_per_section(self) -> None:
        chunks = chunk_markdown(_DOC, tenant_id="retail-demo", source_name="gl_coding_policy.md")
        assert len(chunks) == 2
        assert {c.section for c in chunks} == {
            "1. Professional Services",
            "2. Software Subscriptions",
        }

    def test_title_is_not_a_chunk(self) -> None:
        chunks = chunk_markdown(_DOC, tenant_id="retail-demo", source_name="gl_coding_policy.md")
        assert all("Retail — GL Coding Policy" not in c.section for c in chunks if c.section)

    def test_citation_ref_uses_clause_number(self) -> None:
        chunks = chunk_markdown(_DOC, tenant_id="retail-demo", source_name="gl_coding_policy.md")
        refs = {c.citation_ref for c in chunks}
        assert "gl_coding_policy.md#1" in refs
        assert "gl_coding_policy.md#2" in refs

    def test_heading_is_prepended_to_content(self) -> None:
        chunks = chunk_markdown(_DOC, tenant_id="retail-demo", source_name="gl_coding_policy.md")
        first = next(c for c in chunks if c.citation_ref == "gl_coding_policy.md#1")
        assert first.content.startswith("1. Professional Services")
        assert "6500" in first.content

    def test_metadata_carried_onto_every_chunk(self) -> None:
        chunks = chunk_markdown(_DOC, tenant_id="retail-demo", source_name="gl_coding_policy.md")
        for c in chunks:
            assert c.tenant_id == "retail-demo"
            assert c.version == "2.0.0"
            assert c.effective_from == date(2026, 1, 1)
            assert c.doc_type == "accounting_policy"

    def test_over_length_section_is_split_on_paragraphs(self) -> None:
        big_body = "\n\n".join([f"Paragraph {i} " + "word " * 80 for i in range(20)])
        text = (
            "# Doc\n\nVersion: 1.0.0\nEffective from: 2026-01-01\n\n"
            f"## 1. Big Section\n\n{big_body}\n"
        )
        chunks = chunk_markdown(text, tenant_id="t", source_name="gl_coding_policy.md")
        assert len(chunks) >= 2
        assert all(len(c.content) <= MAX_CHUNK_CHARS + 200 for c in chunks)
        # Split parts get distinct, suffixed citation anchors.
        refs = [c.citation_ref for c in chunks]
        assert len(refs) == len(set(refs))

    def test_document_with_no_sections_fails_loud(self) -> None:
        text = "# Only a title\n\nVersion: 1.0.0\nEffective from: 2026-01-01\n"
        with pytest.raises(RetrievalError, match="no chunks"):
            chunk_markdown(text, tenant_id="t", source_name="gl_coding_policy.md")
