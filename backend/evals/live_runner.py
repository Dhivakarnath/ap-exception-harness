"""Live eval paths: extraction, RAG triad, and CallbackHandler agent spans."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ap_agent.agent.context_slicing import coding_slice
from ap_agent.agent.supervisor import Supervisor, new_run_state
from ap_agent.extract.extractor import InvoiceExtractor
from ap_agent.ingest.events import DocumentPayload
from ap_agent.ingest.parsing import parse_payload
from apfixtures.manifest import ManifestEntry, load_manifest
from apfixtures.spec import FailureMode
from evals.grading import compare_extraction_fields

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = REPO_ROOT / "datasets" / "generated"
LIVE_RAG_MODES = (
    FailureMode.NON_PO_SERVICES,
    FailureMode.CLEAN_TOUCHLESS,
)


def _has_aws_credentials() -> bool:
    import os

    return bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))


def _invoice_to_field_dict(invoice: object) -> dict[str, object]:
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


def run_live_extraction_sample(
    entry: ManifestEntry,
    *,
    extractor: InvoiceExtractor | None = None,
) -> tuple[float | None, list[str]]:
    """Extract one manifest invoice and grade against ground truth."""
    if not _has_aws_credentials():
        return None, ["AWS credentials not available"]

    pdf_path = DATASET_ROOT / "invoices" / entry.filename
    if not pdf_path.exists():
        return None, [f"missing file {pdf_path}"]

    if extractor is None:
        from ap_agent.extract.bedrock import BedrockModelClient

        extractor = InvoiceExtractor(BedrockModelClient())
    ext = extractor
    payload = DocumentPayload(
        filename=entry.filename,
        media_type=entry.media_type,
        content=pdf_path.read_bytes(),
    )
    parsed = parse_payload(payload, document_id=f"doc-{entry.case_id}")
    outcome = ext.extract(
        parsed,
        tenant_id=entry.tenant_id,
        document_id=f"doc-{entry.case_id}",
        invoice_id=entry.case_id,
    )
    if outcome.invoice is None:
        return 0.0, ["extraction returned no invoice"]

    expected = entry.expected_extraction.model_dump()
    actual = _invoice_to_field_dict(outcome.invoice)
    matches, total, mismatches = compare_extraction_fields(expected, actual)
    score = round(matches / total, 4) if total else None
    return score, mismatches


def run_live_rag_cases(
    supervisor: Supervisor,
    entries: list[ManifestEntry],
) -> list[dict[str, Any]]:
    """Run non-PO invoices through the supervisor; collect RAG triad inputs."""
    from apfixtures.cases import build_case
    from evals.policy_context import _invoice_from_case, _parse_case_index

    rag_cases: list[dict[str, Any]] = []
    for entry in entries:
        mode, index = _parse_case_index(entry.case_id)
        case = build_case(mode, index)
        invoice = _invoice_from_case(case.content, case.case_id, case.tenant_id)
        if not invoice.is_non_po:
            continue
        state = new_run_state(
            tenant_id=case.tenant_id,
            invoice_id=case.case_id,
            document_id=f"doc-{case.case_id}",
            invoice=invoice,
        )
        try:
            from deepeval.integrations.langchain import CallbackHandler

            supervisor._deps.langchain_callbacks = [CallbackHandler(thread_id=state.run_id)]  # type: ignore[attr-defined]
        except ImportError:
            pass

        result = supervisor.run(state)
        coding = result.coding_result
        if coding is None or not coding.retrieval_context:
            continue
        query = coding_slice(invoice, []).invoice_summary
        output = coding.reasoning
        if coding.coding is not None:
            output = f"GL {coding.coding.gl_account}: {coding.reasoning}"
        rag_cases.append(
            {
                "input": query,
                "actual_output": output,
                "retrieval_context": list(coding.retrieval_context),
                "case_id": entry.case_id,
            }
        )
    return rag_cases


def pick_live_sample_entries(manifest_path: Path, *, per_mode: int = 1) -> list[ManifestEntry]:
    manifest = load_manifest(manifest_path)
    picked: list[ManifestEntry] = []
    for mode in LIVE_RAG_MODES:
        mode_entries = [e for e in manifest.entries if e.failure_mode == mode]
        picked.extend(mode_entries[:per_mode])
    return picked


def build_live_supervisor(tenant_id: str) -> Supervisor:
    from ap_agent.api.supervisor_factory import build_supervisor

    return build_supervisor(tenant_id=tenant_id, live=True, mcp=False)
