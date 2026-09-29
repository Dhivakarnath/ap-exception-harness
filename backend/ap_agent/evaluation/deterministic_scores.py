"""Deterministic per-run scores — extraction accuracy and policy adherence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ap_agent.agent.state import RunState
from ap_agent.policy.engine import CHECK_NAMES
from apfixtures.manifest import DatasetManifest, ManifestEntry, load_manifest
from evals.grading import compare_extraction_fields

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST_PATH = _REPO_ROOT / "datasets" / "generated" / "manifest.json"

GroundTruthStatus = Literal["matched", "unknown", "foreign"]


@dataclass(frozen=True, slots=True)
class _ManifestIndexes:
    by_case: dict[str, ManifestEntry]
    by_tenant_hash: dict[tuple[str, str], ManifestEntry]
    by_hash: dict[str, list[ManifestEntry]]


def _load_indexes() -> _ManifestIndexes | None:
    if not _MANIFEST_PATH.is_file():
        return None
    manifest = load_manifest(_MANIFEST_PATH)
    return _indexes_for(manifest)


def _indexes_for(manifest: DatasetManifest) -> _ManifestIndexes:
    by_case = {entry.case_id: entry for entry in manifest.entries}
    by_tenant_hash: dict[tuple[str, str], ManifestEntry] = {}
    by_hash: dict[str, list[ManifestEntry]] = {}
    for entry in manifest.entries:
        by_tenant_hash[(entry.tenant_id, entry.content_sha256)] = entry
        by_hash.setdefault(entry.content_sha256, []).append(entry)
    return _ManifestIndexes(
        by_case=by_case,
        by_tenant_hash=by_tenant_hash,
        by_hash=by_hash,
    )


def _looks_like_content_hash(document_id: str) -> bool:
    return (
        len(document_id) == 64 and all(c in "0123456789abcdef" for c in document_id)
    )


def _content_hash_for_state(state: RunState) -> str | None:
    """Resolve the ingress SHA-256 for manifest lookup.

    Upload runs persist a UUID ``document_id`` on ``RunState``; the digest lives
    on ``documents.content_hash``. Fixture/demo paths may use ``doc-{case_id}``
    or the raw hash directly.
    """
    document_id = state.document_id.strip()
    if not document_id or document_id.startswith("doc-"):
        return None
    if _looks_like_content_hash(document_id):
        return document_id

    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import Document

    try:
        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is not None:
                return str(document.content_hash)
    except Exception:
        return None
    return None


def manifest_entry_for_state(state: RunState) -> ManifestEntry | None:
    """Resolve a manifest row for this run within the run's tenant.

    Lookup order:
    1. ``invoice_id == case_id`` (tenant must match)
    2. ``document_id == doc-{case_id}`` (tenant must match)
    3. ``(tenant_id, content_sha256)`` for generic uploads
    """
    indexes = _load_indexes()
    if indexes is None:
        return None

    tenant_id = state.tenant_id
    if state.invoice_id in indexes.by_case:
        entry = indexes.by_case[state.invoice_id]
        if entry.tenant_id == tenant_id:
            return entry

    doc_key = (
        state.document_id.removeprefix("doc-")
        if state.document_id.startswith("doc-")
        else state.document_id
    )
    if doc_key in indexes.by_case:
        entry = indexes.by_case[doc_key]
        if entry.tenant_id == tenant_id:
            return entry

    content_hash = _content_hash_for_state(state)
    if content_hash is not None:
        return indexes.by_tenant_hash.get((tenant_id, content_hash))

    return None


def _foreign_catalog_entry(state: RunState) -> ManifestEntry | None:
    """Manifest row for this document bytes under a different tenant, if any."""
    indexes = _load_indexes()
    content_hash = _content_hash_for_state(state)
    if indexes is None or content_hash is None:
        return None
    matches = [
        entry
        for entry in indexes.by_hash.get(content_hash, [])
        if entry.tenant_id != state.tenant_id
    ]
    if not matches:
        return None
    return matches[0]


def _extraction_na_details(
    state: RunState,
    *,
    status: GroundTruthStatus,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "applicable": False,
        "ground_truth_status": status,
        "tenant_id": state.tenant_id,
        "reason": reason,
        **extra,
    }


def _invoice_field_dict(invoice: object) -> dict[str, object]:
    inv = invoice
    return {
        "invoice_number": str(inv.invoice_number.value),  # type: ignore[attr-defined]
        "invoice_date": str(inv.invoice_date.value),
        "vendor_name_printed": str(inv.vendor_name.value),
        "currency": str(inv.currency.value),
        "subtotal": str(inv.subtotal.value.amount),
        "total_amount": str(inv.total_amount.value.amount),
        "po_reference": (
            str(inv.po_reference.value) if inv.po_reference is not None else None
        ),
    }


def score_extraction_accuracy(state: RunState) -> tuple[float | None, dict[str, Any]]:
    """Grade extracted header fields against tenant-scoped manifest ground truth."""
    if state.invoice is None:
        return None, _extraction_na_details(
            state,
            status="unknown",
            reason="no_invoice_on_run",
        )

    entry = manifest_entry_for_state(state)
    if entry is None:
        foreign = _foreign_catalog_entry(state)
        if foreign is not None:
            return None, _extraction_na_details(
                state,
                status="foreign",
                reason=(
                    f"Document is catalogued under {foreign.tenant_id}, "
                    f"not {state.tenant_id}."
                ),
                catalog_tenant=foreign.tenant_id,
                catalog_case_id=foreign.case_id,
            )
        return None, _extraction_na_details(
            state,
            status="unknown",
            reason=f"No ground truth for this document in {state.tenant_id}.",
        )

    expected = entry.expected_extraction.model_dump()
    actual = _invoice_field_dict(state.invoice)
    matches, total, mismatches = compare_extraction_fields(expected, actual)
    score = round(matches / total, 4) if total else None
    return score, {
        "applicable": True,
        "ground_truth_status": "matched",
        "ground_truth_tenant": entry.tenant_id,
        "tenant_id": state.tenant_id,
        "source": "manifest_ground_truth",
        "case_id": entry.case_id,
        "matches": matches,
        "total": total,
        "mismatches": mismatches[:5],
    }


def score_policy_adherence(state: RunState) -> tuple[float | None, dict[str, Any]]:
    """Score policy checks on the actual run ledger."""
    entry = manifest_entry_for_state(state)
    if entry is not None:
        return _score_policy_against_manifest(state, entry)
    return _score_policy_from_ledger(state)


def _score_policy_against_manifest(
    state: RunState,
    entry: ManifestEntry,
) -> tuple[float | None, dict[str, Any]]:
    expected_checks = {
        item["name"]: item["verdict"]
        for item in entry.expected_checks
        if item["name"] in CHECK_NAMES
    }
    if not expected_checks:
        return _score_policy_from_ledger(state)

    matches = 0
    mismatches: list[str] = []
    for name, expected_verdict in expected_checks.items():
        actual = state.ledger.by_name(name)
        if actual is None:
            mismatches.append(f"{name}: missing from ledger")
            continue
        if actual.verdict.value == expected_verdict:
            matches += 1
        else:
            mismatches.append(
                f"{name}: expected {expected_verdict}, got {actual.verdict.value}"
            )

    total = len(expected_checks)
    score = round(matches / total, 4) if total else None
    return score, {
        "applicable": True,
        "source": "manifest_expected_checks",
        "case_id": entry.case_id,
        "ground_truth_tenant": entry.tenant_id,
        "matches": matches,
        "total": total,
        "mismatches": mismatches[:5],
    }


def _score_policy_from_ledger(state: RunState) -> tuple[float | None, dict[str, Any]]:
    applicable = [
        result for result in state.ledger.results if result.verdict.value != "skip"
    ]
    if not applicable:
        return None, {"applicable": False, "reason": "no_applicable_policy_checks"}

    passed = sum(1 for result in applicable if result.verdict.value == "pass")
    flagged = sum(1 for result in applicable if result.verdict.value == "flag")
    failed = sum(1 for result in applicable if result.verdict.value == "fail")
    score = round(passed / len(applicable), 4)
    return score, {
        "applicable": True,
        "source": "ledger_pass_rate",
        "passed": passed,
        "flagged": flagged,
        "failed": failed,
        "total": len(applicable),
    }
