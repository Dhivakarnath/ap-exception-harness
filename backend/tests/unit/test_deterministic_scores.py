"""Unit tests for live deterministic extraction and policy scores."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ap_agent.agent.supervisor import new_run_state
from ap_agent.core.canonical import Invoice
from ap_agent.core.checks import CheckCategory, CheckLedger, CheckResult, Severity, Verdict
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money
from ap_agent.evaluation.deterministic_scores import (
    score_extraction_accuracy,
    score_policy_adherence,
)
from apfixtures.manifest import load_manifest

REPO_MANIFEST = (
    Path(__file__).resolve().parents[3] / "datasets" / "generated" / "manifest.json"
)


def _ex(value: object, confidence: float = 0.99) -> Extracted[object]:
    return Extracted(value=value, confidence=confidence, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="USD")


def _invoice_from_entry(entry) -> Invoice:
    expected = entry.expected_extraction
    return Invoice(
        invoice_id=entry.case_id,
        tenant_id=entry.tenant_id,
        document_id=f"doc-{entry.case_id}",
        invoice_number=_ex(expected.invoice_number),
        invoice_date=_ex(date.fromisoformat(expected.invoice_date)),
        vendor_name=_ex(expected.vendor_name_printed),
        currency=_ex(expected.currency),
        subtotal=_ex(_money(expected.subtotal)),
        total_amount=_ex(_money(expected.total_amount)),
        po_reference=_ex(expected.po_reference) if expected.po_reference else None,
        lines=(),
    )


@pytest.fixture
def manifest_entry():
    if not REPO_MANIFEST.is_file():
        pytest.skip("generated manifest not present — run make dataset-quick")
    manifest = load_manifest(REPO_MANIFEST)
    return manifest.entries[0]


def test_extraction_scores_against_manifest(manifest_entry, monkeypatch) -> None:
    monkeypatch.setattr(
        "ap_agent.evaluation.deterministic_scores._MANIFEST_PATH",
        REPO_MANIFEST,
    )
    state = new_run_state(
        tenant_id=manifest_entry.tenant_id,
        invoice_id=manifest_entry.case_id,
        document_id=f"doc-{manifest_entry.case_id}",
        invoice=_invoice_from_entry(manifest_entry),
    )
    score, details = score_extraction_accuracy(state)
    assert score == 1.0
    assert details["applicable"] is True
    assert details["matches"] == 7


def test_extraction_not_applicable_without_manifest_link() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="upload-abc",
        document_id="doc-upload",
        invoice=Invoice(
            invoice_id="upload-abc",
            tenant_id="retail-demo",
            document_id="doc-upload",
            invoice_number=_ex("INV-1"),
            invoice_date=_ex(date(2026, 1, 1)),
            vendor_name=_ex("Vendor"),
            currency=_ex("USD"),
            subtotal=_ex(_money("10.00")),
            total_amount=_ex(_money("10.00")),
            lines=(),
        ),
    )
    score, details = score_extraction_accuracy(state)
    assert score is None
    assert details["applicable"] is False
    assert details["ground_truth_status"] == "unknown"


def test_extraction_scores_upload_by_persisted_document_uuid(
    manifest_entry, monkeypatch
) -> None:
    """Upload runs carry a UUID document_id; hash comes from the documents row."""
    from contextlib import contextmanager

    monkeypatch.setattr(
        "ap_agent.evaluation.deterministic_scores._MANIFEST_PATH",
        REPO_MANIFEST,
    )

    class _FakeDocument:
        content_hash = manifest_entry.content_sha256

    @contextmanager
    def _fake_scope():
        class _Session:
            def get(self, _model, document_id: str):
                return _FakeDocument() if document_id == "uuid-doc-1" else None

        yield _Session()

    monkeypatch.setattr("ap_agent.persistence.db.session_scope", _fake_scope)
    state = new_run_state(
        tenant_id=manifest_entry.tenant_id,
        invoice_id="upload-deadbeef",
        document_id="uuid-doc-1",
        invoice=_invoice_from_entry(manifest_entry),
    )
    score, details = score_extraction_accuracy(state)
    assert score == 1.0
    assert details["ground_truth_status"] == "matched"


def test_extraction_scores_upload_by_content_hash(manifest_entry, monkeypatch) -> None:
    monkeypatch.setattr(
        "ap_agent.evaluation.deterministic_scores._MANIFEST_PATH",
        REPO_MANIFEST,
    )
    state = new_run_state(
        tenant_id=manifest_entry.tenant_id,
        invoice_id="upload-deadbeef",
        document_id=manifest_entry.content_sha256,
        invoice=_invoice_from_entry(manifest_entry),
    )
    score, details = score_extraction_accuracy(state)
    assert score == 1.0
    assert details["applicable"] is True
    assert details["ground_truth_status"] == "matched"
    assert details["ground_truth_tenant"] == manifest_entry.tenant_id
    assert details["case_id"] == manifest_entry.case_id


def test_extraction_foreign_catalog_when_wrong_tenant(manifest_entry, monkeypatch) -> None:
    monkeypatch.setattr(
        "ap_agent.evaluation.deterministic_scores._MANIFEST_PATH",
        REPO_MANIFEST,
    )
    other_tenant = (
        "manufacturing-demo"
        if manifest_entry.tenant_id == "retail-demo"
        else "retail-demo"
    )
    state = new_run_state(
        tenant_id=other_tenant,
        invoice_id="upload-deadbeef",
        document_id=manifest_entry.content_sha256,
        invoice=_invoice_from_entry(manifest_entry),
    )
    score, details = score_extraction_accuracy(state)
    assert score is None
    assert details["applicable"] is False
    assert details["ground_truth_status"] == "foreign"
    assert details["catalog_tenant"] == manifest_entry.tenant_id


def test_policy_scores_from_ledger_pass_rate() -> None:
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id="upload-abc",
        document_id="doc-upload",
        invoice=Invoice(
            invoice_id="upload-abc",
            tenant_id="retail-demo",
            document_id="doc-upload",
            invoice_number=_ex("INV-1"),
            invoice_date=_ex(date(2026, 1, 1)),
            vendor_name=_ex("Vendor"),
            currency=_ex("USD"),
            subtotal=_ex(_money("10.00")),
            total_amount=_ex(_money("10.00")),
            lines=(),
        ),
    )
    state.ledger = CheckLedger(
        results=(
            CheckResult(
                name="vendor_active",
                category=CheckCategory.IDENTITY,
                verdict=Verdict.PASS,
                severity=Severity.LOW,
                reasoning="ok",
            ),
            CheckResult(
                name="duplicate_invoice",
                category=CheckCategory.DUPLICATE,
                verdict=Verdict.SKIP,
                severity=Severity.INFO,
                reasoning="n/a",
            ),
            CheckResult(
                name="amount_ceiling",
                category=CheckCategory.GUARDRAIL,
                verdict=Verdict.FLAG,
                severity=Severity.MEDIUM,
                reasoning="high",
            ),
        )
    )
    score, details = score_policy_adherence(state)
    assert score == 0.5
    assert details["source"] == "ledger_pass_rate"
    assert details["passed"] == 1
    assert details["total"] == 2


def test_policy_scores_against_manifest_expected_checks(manifest_entry, monkeypatch) -> None:
    monkeypatch.setattr(
        "ap_agent.evaluation.deterministic_scores._MANIFEST_PATH",
        REPO_MANIFEST,
    )
    state = new_run_state(
        tenant_id=manifest_entry.tenant_id,
        invoice_id=manifest_entry.case_id,
        document_id=f"doc-{manifest_entry.case_id}",
        invoice=_invoice_from_entry(manifest_entry),
    )
    first_check = manifest_entry.expected_checks[0]
    state.ledger = CheckLedger(
        results=(
            CheckResult(
                name=str(first_check["name"]),
                category=CheckCategory.IDENTITY,
                verdict=Verdict(str(first_check["verdict"])),
                severity=Severity.LOW,
                reasoning="ok",
            ),
        )
    )
    score, details = score_policy_adherence(state)
    assert score is not None
    assert details["source"] == "manifest_expected_checks"
