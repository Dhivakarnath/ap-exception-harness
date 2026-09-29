"""Structure-aware chunking of policy documents (FR-6.3).

Chunks on **section boundaries**, not blind fixed-size windows. Accounting
policy and coding precedent are written in numbered clauses and named sections,
and a clause is the natural unit of citation: "account 6500 applies to
contract services" means something whole on its own, whereas a 512-token window
cutting across two clauses would cite a fragment that grounds neither.

The corpus is authored as markdown with a small front-matter block (version,
effective dates) and `##`/`###` headings, so a heading-aware split gives one
chunk per clause with its heading retained as the section label and citation
anchor. That is deliberately simpler than running these already-clean markdown
files back through Docling: Docling earns its cost on *scanned invoices* where
layout must be recovered from pixels, but the policy corpus is text we wrote,
so a layout model would add latency and dependency for no recovered
information. The ingest pipeline still *accepts* a Docling-parsed document for
the case where a customer hands us a PDF policy, but for the shipped markdown
corpus this heading splitter is the right tool.

Each chunk carries the metadata the retrieval pre-filter needs — tenant,
doc_type, version, effective dates, section — and a stable `citation_ref`
(`source_name#section`) that satisfies the no-ungrounded-coding rule (FR-5.3):
a code can only be applied if it points back to one of these refs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ap_agent.errors import ErrorContext, RetrievalError

# A chunk longer than this many characters is split further on paragraph
# boundaries. Set well under Titan V2's 50k-char ceiling and small enough that
# a single retrieved chunk stays a focused, citable unit rather than a whole
# multi-clause section.
MAX_CHUNK_CHARS = 1_800

_FRONT_MATTER_RE = re.compile(r"^Version:\s*(?P<version>\S+)", re.MULTILINE)
_EFFECTIVE_FROM_RE = re.compile(r"^Effective from:\s*(?P<d>\d{4}-\d{2}-\d{2})", re.MULTILINE)
_EFFECTIVE_TO_RE = re.compile(r"^Effective to:\s*(?P<d>\d{4}-\d{2}-\d{2})", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(?P<title>.+?)\s*$", re.MULTILINE)

# Filename-stem prefix -> `policy_chunks.doc_type`. The DB CHECK constraint only
# permits three doc_type values, so every corpus filename must start with one of
# these prefixes. Subfolders are organisational only: `doc_type_for` resolves on
# the basename, so `policy/gl_coding_policy_office_supplies.md` is still an
# `accounting_policy`. Longer, descriptive stems (e.g. per-account guides) are
# fine as long as they start with an allowed prefix.
_DOC_TYPE_BY_STEM: dict[str, str] = {
    "gl_coding_policy": "accounting_policy",
    "coding_precedent": "coding_precedent",
    "vendor_contract": "vendor_contract",
}


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    """Front-matter parsed from a corpus document."""

    version: str
    effective_from: date
    effective_to: date | None


@dataclass(frozen=True, slots=True)
class Chunk:
    """One citable unit of the corpus, ready to embed and store."""

    tenant_id: str
    doc_type: str
    source_name: str
    section: str | None
    citation_ref: str
    version: str
    effective_from: date
    effective_to: date | None
    content: str
    token_estimate: int = 0
    chunk_metadata: dict[str, object] = field(default_factory=dict)


def doc_type_for(source_name: str) -> str:
    """Map a corpus filename to a `policy_chunks.doc_type` value.

    Fails loud on an unrecognised stem rather than defaulting: the CHECK
    constraint on the column only permits three values, and silently picking
    one would mis-scope every chunk from that file.
    """
    stem = source_name.rsplit("/", 1)[-1].removesuffix(".md")
    # Superseded variants keep their base type (e.g. `gl_coding_policy_v1...`).
    for key, value in _DOC_TYPE_BY_STEM.items():
        if stem.startswith(key):
            return value
    raise RetrievalError(
        f"Cannot determine doc_type for corpus file {source_name!r}. "
        f"Expected a name starting with one of {sorted(_DOC_TYPE_BY_STEM)}.",
        context=ErrorContext(stage="rag.chunking", inputs={"source": source_name}),
    )


def parse_front_matter(text: str, source_name: str) -> DocumentMeta:
    """Extract version and effective dates from the document's front matter.

    These drive the retrieval pre-filter (FR-6.3), so a missing version or
    effective_from is fatal: a chunk with no version cannot be scoped, and an
    unscoped chunk could be cited when a superseding one should win.
    """
    version_match = _FRONT_MATTER_RE.search(text)
    from_match = _EFFECTIVE_FROM_RE.search(text)
    if version_match is None or from_match is None:
        raise RetrievalError(
            f"Corpus file {source_name!r} is missing required front matter "
            "(Version and Effective from). Refusing to ingest an unscopable "
            "document.",
            context=ErrorContext(stage="rag.chunking", inputs={"source": source_name}),
        )
    to_match = _EFFECTIVE_TO_RE.search(text)
    return DocumentMeta(
        version=version_match.group("version"),
        effective_from=date.fromisoformat(from_match.group("d")),
        effective_to=date.fromisoformat(to_match.group("d")) if to_match else None,
    )


def _slug(title: str) -> str:
    """A stable citation anchor from a heading, e.g. '1. Professional...' -> '1'."""
    leading = re.match(r"^\s*(\d+(?:\.\d+)*)", title)
    if leading:
        return leading.group(1)
    return re.sub(r"[^a-z0-9]+", "-", title.strip().casefold()).strip("-")[:48] or "section"


def _split_long(content: str) -> list[str]:
    """Split an over-length section on blank lines, keeping paragraphs whole."""
    if len(content) <= MAX_CHUNK_CHARS:
        return [content]
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    pieces: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}".strip() if current else para
        if len(candidate) > MAX_CHUNK_CHARS and current:
            pieces.append(current)
            current = para
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_markdown(text: str, *, tenant_id: str, source_name: str) -> list[Chunk]:
    """Split a corpus markdown document into citable, metadata-tagged chunks.

    One chunk per `##`/`###` section (further split only if a section exceeds
    `MAX_CHUNK_CHARS`). The front matter and the document title (`#`) are
    metadata, not content, so they are not embedded as their own chunk.
    """
    meta = parse_front_matter(text, source_name)
    doc_type = doc_type_for(source_name)

    headings = list(_HEADING_RE.finditer(text))
    chunks: list[Chunk] = []

    for index, match in enumerate(headings):
        level = len(match.group(1))
        if level == 1:
            # Document title — metadata, not a citable clause.
            continue
        title = match.group("title").strip()
        body_start = match.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[body_start:body_end].strip()
        if not body:
            continue

        anchor = _slug(title)
        for part_no, piece in enumerate(_split_long(body)):
            # Multi-part sections get a suffixed anchor so each remains a
            # distinct, unambiguous citation target.
            ref_anchor = anchor if part_no == 0 else f"{anchor}.{part_no}"
            citation_ref = f"{source_name}#{ref_anchor}"
            chunks.append(
                Chunk(
                    tenant_id=tenant_id,
                    doc_type=doc_type,
                    source_name=source_name,
                    section=title,
                    citation_ref=citation_ref,
                    version=meta.version,
                    effective_from=meta.effective_from,
                    effective_to=meta.effective_to,
                    # The heading is prepended to the embedded text so the
                    # section's subject is part of what gets vectorised — "account
                    # 6500" retrieves far better when "Professional and Contract
                    # Services" sits in the same chunk.
                    content=f"{title}\n\n{piece}",
                    token_estimate=len(piece) // 4,
                    chunk_metadata={"heading": title, "part": part_no},
                )
            )

    if not chunks:
        raise RetrievalError(
            f"Corpus file {source_name!r} produced no chunks. A policy document "
            "with no ## sections cannot be cited clause by clause.",
            context=ErrorContext(stage="rag.chunking", inputs={"source": source_name}),
        )
    return chunks
