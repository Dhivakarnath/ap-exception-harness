"""Ground-truth manifest.

The manifest is what makes the dataset gradeable. For every artefact it records
the values extraction should recover, the verdict each check should reach, and
the route the invoice should take — plus a content hash so a stale manifest is
detectable rather than silently wrong.

Written as JSON so the eval harness, the frontend, and a human reading the repo
all consume the same file.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apfixtures.spec import DATASET_SEED_VERSION, FailureMode, InvoiceCase

MANIFEST_SCHEMA_VERSION = "1.0.0"


class ExpectedExtraction(BaseModel):
    """Field values a correct extraction must recover.

    Amounts are strings so the manifest carries exact decimals; a JSON float
    would reintroduce the rounding error the domain layer forbids.
    """

    model_config = ConfigDict(frozen=True)

    invoice_number: str
    invoice_date: str
    due_date: str | None
    vendor_name_printed: str
    expected_vendor_id: str
    currency: str
    subtotal: str
    tax_amount: str | None
    total_amount: str
    po_reference: str | None
    payment_terms: str | None
    remit_to_last4: str | None
    line_count: int
    line_totals: list[str]


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    failure_mode: FailureMode
    tenant_id: str
    filename: str
    render_format: str
    media_type: str
    content_sha256: str
    size_bytes: int

    noise_level: float
    rotation_degrees: float
    dpi: int

    expected_extraction: ExpectedExtraction
    expected_route: str
    expected_checks: list[dict[str, Any]]
    requires_human_review: bool
    expected_gl_account: str | None
    min_extraction_confidence: float | None
    rationale: str


class DatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = MANIFEST_SCHEMA_VERSION
    seed_version: str = DATASET_SEED_VERSION
    generator_seed: int
    generated_at: str
    cases_per_mode: int
    total_cases: int
    mode_counts: dict[str, int]
    format_counts: dict[str, int]
    entries: list[ManifestEntry]

    def by_mode(self, mode: FailureMode) -> list[ManifestEntry]:
        return [e for e in self.entries if e.failure_mode is mode]


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def media_type_for(filename: str) -> str:
    return "image/png" if filename.endswith(".png") else "application/pdf"


def build_entry(
    case: InvoiceCase,
    payload: bytes,
    *,
    filename: str | None = None,
) -> ManifestEntry:
    content = case.content
    expectation = case.expectation

    return ManifestEntry(
        case_id=case.case_id,
        failure_mode=case.failure_mode,
        tenant_id=case.tenant_id,
        filename=filename or case.filename,
        render_format=case.render_format.value,
        media_type=media_type_for(case.filename),
        content_sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        noise_level=case.noise_level,
        rotation_degrees=case.rotation_degrees,
        dpi=case.dpi,
        expected_extraction=ExpectedExtraction(
            invoice_number=content.invoice_number,
            invoice_date=content.invoice_date.isoformat(),
            due_date=content.due_date.isoformat() if content.due_date else None,
            vendor_name_printed=content.vendor_name_printed,
            expected_vendor_id=content.vendor_id,
            currency=content.currency,
            subtotal=format(content.subtotal, "f"),
            tax_amount=_s(content.tax_amount),
            total_amount=format(content.total_amount, "f"),
            po_reference=content.po_reference,
            payment_terms=content.payment_terms,
            remit_to_last4=content.remit_to_last4,
            line_count=len(content.lines),
            line_totals=[format(line.line_total, "f") for line in content.lines],
        ),
        expected_route=expectation.route.value,
        expected_checks=[
            {"name": c.name, "verdict": c.verdict.value, "note": c.note}
            for c in expectation.checks
        ],
        requires_human_review=expectation.requires_human_review,
        expected_gl_account=expectation.expected_gl_account,
        min_extraction_confidence=expectation.min_extraction_confidence,
        rationale=expectation.rationale,
    )


def build_manifest(
    entries: list[ManifestEntry], *, generator_seed: int, cases_per_mode: int
) -> DatasetManifest:
    return DatasetManifest(
        generator_seed=generator_seed,
        generated_at=datetime.now(UTC).isoformat(),
        cases_per_mode=cases_per_mode,
        total_cases=len(entries),
        mode_counts=dict(Counter(e.failure_mode.value for e in entries)),
        format_counts=dict(Counter(e.render_format for e in entries)),
        entries=entries,
    )


def write_manifest(manifest: DatasetManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def load_manifest(path: Path) -> DatasetManifest:
    if not path.is_file():
        raise FileNotFoundError(
            f"Manifest not found at {path}. Run `make dataset` to generate the "
            "adversarial dataset first."
        )
    return DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))


class ManifestDrift(BaseModel):
    """Differences between a manifest and the files on disk."""

    model_config = ConfigDict(frozen=True)

    missing_files: list[str] = Field(default_factory=list)
    hash_mismatches: list[str] = Field(default_factory=list)
    unexpected_files: list[str] = Field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not (self.missing_files or self.hash_mismatches or self.unexpected_files)

    def describe(self) -> str:
        if self.is_clean:
            return "manifest and files agree"
        parts: list[str] = []
        if self.missing_files:
            parts.append(f"{len(self.missing_files)} missing: {self.missing_files[:5]}")
        if self.hash_mismatches:
            parts.append(f"{len(self.hash_mismatches)} changed: {self.hash_mismatches[:5]}")
        if self.unexpected_files:
            parts.append(f"{len(self.unexpected_files)} unexpected: {self.unexpected_files[:5]}")
        return "; ".join(parts)


def verify_manifest(manifest: DatasetManifest, files_dir: Path) -> ManifestDrift:
    """Check every manifest entry against the artefact on disk.

    A hash mismatch means the manifest no longer describes the files, so any eval
    run against them would be grading the wrong thing.
    """
    missing: list[str] = []
    mismatched: list[str] = []
    expected_paths: set[Path] = set()

    for entry in manifest.entries:
        rel_path = Path(entry.filename)
        expected_paths.add(rel_path)
        path = files_dir / rel_path
        if not path.is_file():
            missing.append(entry.filename)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.content_sha256:
            mismatched.append(entry.filename)

    actual_paths = {p.relative_to(files_dir) for p in files_dir.rglob("*") if p.is_file()}
    unexpected = sorted(str(p) for p in actual_paths - expected_paths)

    return ManifestDrift(
        missing_files=sorted(missing),
        hash_mismatches=sorted(mismatched),
        unexpected_files=unexpected,
    )
