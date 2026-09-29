"""Read-only platform endpoints for the transparency UI's dedicated pages.

These expose the things a reviewer needs to *see* about the system itself —
the active policy pack, the defence-in-depth guardrails, and the evaluation
scorecard — as first-party API data rather than hard-coded in the frontend.
Everything here is derived from the real configuration/code, so the pages show
what the system actually enforces, not a marketing description of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError, ProgrammingError

from ap_agent.config import get_settings

router = APIRouter(tags=["platform"])

# Real generated invoice PDFs, used to give the document viewer a genuine page
# image to render (the highlight-back surface). Resolved relative to the repo.
_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "datasets" / "generated" / "invoices"


@router.get("/documents/{name}/image")
def document_image(name: str) -> Response:
    """Render a real fixture invoice's first page to PNG for the document viewer.

    `name` is a fixture scenario stem (e.g. ``clean_touchless``); the first
    matching generated PDF is rasterised with the same `render_page_images`
    used by the multimodal extractor, so the image the reviewer sees is the
    actual source document, not a mock. Returns 404 when no fixture matches.
    """
    from ap_agent.ingest.parsing import render_page_images

    # Guard against path traversal: only a plain stem is allowed.
    safe = name.replace("/", "").replace("..", "")
    matches = sorted(_FIXTURE_DIR.glob(f"{safe}*.pdf")) if _FIXTURE_DIR.is_dir() else []
    if not matches:
        return JSONResponse({"error": f"no fixture for {name!r}"}, status_code=404)
    try:
        images = render_page_images(matches[0], media_type="application/pdf", max_pages=1)
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        return JSONResponse({"error": str(exc)}, status_code=500)
    if not images:
        return JSONResponse({"error": "no page rendered"}, status_code=500)
    _media, data = images[0]
    return Response(content=data, media_type="image/png")


def _policy_pack_path(tenant_id: str) -> Path:
    """Resolve a tenant's policy pack file. Falls back to the retail non-PO pack
    (the one the demo runs use)."""
    packs = get_settings().policy_pack_dir
    candidates = [
        packs / f"{tenant_id}.yaml",
        packs / "retail_non_po.yaml",
        packs / "manufacturing.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


@router.get("/policy")
def get_policy(tenant: str = "retail-demo") -> JSONResponse:
    """The active, versioned policy pack — thresholds, tolerances, DOA matrix.

    This is the *what* half of the policy engine (the *how* is code): the
    declarative configuration that decides touchless eligibility, match
    tolerances, and the delegation-of-authority ladder. Returned verbatim from
    the loaded pack so the page cannot drift from what the engine enforces.
    """
    from ap_agent.core.policy_pack import load_policy_pack

    pack = load_policy_pack(_policy_pack_path(tenant))
    payload = pack.model_dump(mode="json")
    payload["identity"] = pack.identity
    payload["doa_boundaries"] = [str(b) for b in pack.doa.boundaries()]
    return JSONResponse(payload)


@router.get("/guardrails")
def get_guardrails() -> JSONResponse:
    """The three independent layers of defence in depth (FR-9.2, ADR-011).

    Reported from the real scope/token configuration so the page reflects what
    is actually enforced: the agent's own RBAC, the deterministic policy-engine
    middleware at the write boundary, and the MCP servers' server-side scope
    checks — each of which independently refuses an over-privileged action.
    """
    from ap_agent.mcp_servers.permissions import TOKEN_SCOPES

    tokens = {name: sorted(s.value for s in scopes) for name, scopes in TOKEN_SCOPES.items()}
    layers = [
        {
            "id": "agent_rbac",
            "layer": 1,
            "name": "Agent RBAC (toolset filtering)",
            "where": "inside the agent process",
            "enforces": "The agent is only ever given the tools its role needs; "
            "there is no payment tool to call. A sub-agent's toolset is filtered "
            "before the model ever sees it.",
            "blocks_example": "The model cannot invoke a capability it was not "
            "granted, because the tool is absent from its toolset.",
        },
        {
            "id": "policy_engine_middleware",
            "layer": 2,
            "name": "Policy-engine middleware (write gate)",
            "where": "at the tool-call boundary, in-process",
            "enforces": "Every ERP write is wrapped by the deterministic policy "
            "engine. Even a caller that reached the write tool is refused if the "
            "ledger says the invoice is not payable (blocked vendor, "
            "control-account coding, already paid).",
            "blocks_example": "post_bill for a blocked vendor is refused before "
            "it reaches the ERP, independent of what the agent believed.",
        },
        {
            "id": "mcp_server_scopes",
            "layer": 3,
            "name": "MCP server-side scope checks",
            "where": "in the MCP server process (out of the agent's reach)",
            "enforces": "Each MCP tool declares the scope it requires and checks "
            "it against the caller's purpose-scoped token before running — a "
            "Python `if`, not prompt text. A read-only token calling a write "
            "tool is refused server-side, even with the agent's Layer-1 RBAC "
            "removed. There is no payment scope at all.",
            "blocks_example": "The erp-reader token calling post_bill is refused "
            "with PermissionDenied on the server, not the client.",
        },
    ]
    return JSONResponse(
        {
            "layers": layers,
            "tokens": tokens,
            "no_payment_capability": True,
            "note": "There is deliberately no payment scope or tool anywhere in "
            "the system: it terminates at 'approved for payment' (ADR-011).",
        }
    )


_RESULTS_JSON = Path(__file__).resolve().parents[2] / "evals" / "results.json"


def _load_manifest_scorecard() -> dict[str, Any]:
    """CI/manifest scorecard from ``evals/results.json`` (``make eval`` / ``make eval-live``)."""
    if not _RESULTS_JSON.is_file():
        return {
            "status": "not_run",
            "message": "No scorecard yet. Run `make eval` or `make eval-live`.",
        }
    try:
        data = json.loads(_RESULTS_JSON.read_text())
    except json.JSONDecodeError:
        return {"status": "not_run", "message": "Scorecard file is unreadable."}
    if data.get("status") != "available":
        return {
            "status": "not_run",
            "message": data.get("message", "Eval harness has not produced a scorecard."),
        }
    metrics = data.get("metrics") or {}
    return {
        "status": "available",
        "suite": data.get("suite"),
        "generated_at": data.get("generated_at"),
        "note": data.get("note"),
        "policy_adherence": metrics.get("policy_adherence"),
        "routing_label_consistency": metrics.get("routing_label_consistency"),
        "pipeline_cases_passed": data.get("pipeline_cases_passed"),
        "pipeline_cases_total": data.get("pipeline_cases_total"),
        "live": {
            "task_completion": metrics.get("task_completion"),
            "tool_correctness": metrics.get("tool_correctness"),
            "argument_correctness": metrics.get("argument_correctness"),
            "rag_faithfulness": metrics.get("rag_faithfulness"),
            "rag_answer_relevancy": metrics.get("rag_answer_relevancy"),
            "rag_contextual_relevancy": metrics.get("rag_contextual_relevancy"),
            "extraction_accuracy": metrics.get("extraction_accuracy"),
        },
    }


@router.get("/evals")
def get_evals(tenant: str = "all") -> JSONResponse:
    """Real DeepEval results for current and completed live document runs."""
    from sqlalchemy.orm import joinedload

    from ap_agent.persistence.db import session_scope
    from ap_agent.persistence.models import Run, RunEvaluation
    from ap_agent.tenants import ALL_TENANTS

    try:
        with session_scope() as session:
            query = session.query(RunEvaluation).options(
                joinedload(RunEvaluation.run).joinedload(Run.invoice),
                joinedload(RunEvaluation.run).joinedload(Run.decision),
            )
            if tenant != ALL_TENANTS:
                query = query.filter(RunEvaluation.tenant_id == tenant)
            rows = (
                query.order_by(RunEvaluation.created_at.desc())
                .limit(50)
                .all()
            )
            evaluations = [_evaluation_payload(row) for row in rows]
    except (OperationalError, ProgrammingError):
        # Missing database or pre-migration schema is an honest empty state.
        # Programming bugs in the payload must still fail loud.
        evaluations = []

    scorecard = _load_manifest_scorecard()
    if not evaluations:
        return JSONResponse(
            {
                "status": "not_run",
                "message": "No live document has completed DeepEval scoring yet.",
                "manifest_scorecard": scorecard,
                "evaluations": [],
            }
        )
    return JSONResponse(
        {
            "status": "available",
            "manifest_scorecard": scorecard,
            "evaluations": evaluations,
        }
    )


@router.get("/tenants/{tenant_id}/operations-summary")
def operations_summary(tenant_id: str) -> JSONResponse:
    """Operations control-room payload — KPIs, attention, breakdowns, recent runs."""
    from ap_agent.observability.operations_summary import build_operations_summary
    from ap_agent.persistence.db import session_scope

    with session_scope() as session:
        payload = build_operations_summary(session, tenant_id=tenant_id)
    return JSONResponse(payload)


class _DeepTracePreference(BaseModel):
    enabled: bool


@router.get("/platform/observability/status")
def get_observability_status() -> JSONResponse:
    """Deep-trace stack health for the Operations page."""
    from ap_agent.observability.status import observability_status

    return JSONResponse(observability_status())


@router.put("/platform/observability/deep-trace")
def set_deep_trace_preference(body: _DeepTracePreference) -> JSONResponse:
    """Toggle in-process preference for deep trace export on new runs."""
    from ap_agent.observability.status import observability_status, set_deep_trace_preference

    set_deep_trace_preference(body.enabled)
    return JSONResponse(observability_status())


def _invoice_is_non_po(invoice: Any) -> bool | None:
    """Mirror ``Invoice.is_non_po``: no PO reference means non-PO (FR-5.1)."""
    if invoice is None:
        return None
    po_reference = getattr(invoice, "po_reference", None)
    if po_reference is None:
        return True
    return not str(po_reference).strip()


def _evaluation_payload(row: Any) -> dict[str, Any]:
    run = getattr(row, "run", None)
    invoice = getattr(run, "invoice", None) if run is not None else None
    decision = getattr(run, "decision", None) if run is not None else None
    details = row.details or {}
    rag = details.get("rag") or {}
    return {
        "run_id": row.run_id,
        "tenant_id": row.tenant_id,
        "invoice_id": row.invoice_id,
        "invoice_number": getattr(invoice, "invoice_number", None) or row.invoice_id,
        "vendor": getattr(invoice, "vendor_name_raw", None) or "Unknown vendor",
        "is_non_po": _invoice_is_non_po(invoice),
        "route": getattr(decision, "route", None),
        "status": row.status,
        "metrics": {
            "task_completion": row.task_completion,
            "tool_use": row.tool_use,
            "rag_grounding": row.rag_grounding,
            "extraction_accuracy": getattr(row, "extraction_accuracy", None),
            "policy_adherence": getattr(row, "policy_adherence", None),
        },
        "breakdown": _evaluation_breakdown(details),
        "rag_applicable": bool(rag.get("applicable", False)),
        "extraction_applicable": bool((details.get("extraction") or {}).get("applicable", False)),
        "policy_applicable": bool((details.get("policy") or {}).get("applicable", False)),
        "evaluated_after_hitl": bool((details.get("scheduling") or {}).get("after_hitl", False)),
        "judge_model": row.judge_model,
        "error": row.error,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
        "created_at": row.created_at.isoformat(),
    }


def _as_score(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _tool_breakdown(selection: dict[str, Any], argument: dict[str, Any]) -> dict[str, Any]:
    mismatches = _string_list(argument.get("failures"))
    selection_score = _as_score(selection.get("score"))
    selection_reason = selection.get("reason")
    if selection_score is not None and selection_score < 1.0 and selection_reason:
        mismatches = [str(selection_reason), *mismatches]
    argument_reason = argument.get("reason")
    return {
        "selection": selection_score,
        "arguments": _as_score(argument.get("score")),
        "actual": _string_list(selection.get("actual")),
        "expected": _string_list(selection.get("expected")),
        "reason": str(argument_reason) if argument_reason else None,
        "mismatches": mismatches,
    }


def _evaluation_breakdown(details: dict[str, Any]) -> dict[str, Any]:
    selection = details.get("tool_selection") or {}
    argument = details.get("argument_correctness") or {}
    task = details.get("task_completion") or {}
    rag = details.get("rag") or {}
    components = rag.get("components") or {}
    extraction = details.get("extraction") or {}
    policy = details.get("policy") or {}
    task_reason = task.get("reason")
    return {
        "task": {
            "score": _as_score(task.get("score")),
            "reason": str(task_reason) if task_reason else None,
        },
        "tools": _tool_breakdown(selection, argument),
        "rag": {
            "faithfulness": _as_score(components.get("faithfulness")),
            "answer_relevancy": _as_score(components.get("answer_relevancy")),
            "contextual_relevancy": _as_score(components.get("contextual_relevancy")),
        },
        "extraction": {
            "matches": extraction.get("matches"),
            "total": extraction.get("total"),
            "mismatches": _string_list(extraction.get("mismatches")),
            "case_id": extraction.get("case_id"),
            "source": extraction.get("source"),
            "ground_truth_status": extraction.get("ground_truth_status"),
            "ground_truth_tenant": extraction.get("ground_truth_tenant"),
            "catalog_tenant": extraction.get("catalog_tenant"),
            "catalog_case_id": extraction.get("catalog_case_id"),
            "tenant_id": extraction.get("tenant_id"),
            "reason": extraction.get("reason"),
        },
        "policy": {
            "matches": policy.get("matches"),
            "total": policy.get("total"),
            "passed": policy.get("passed"),
            "flagged": policy.get("flagged"),
            "failed": policy.get("failed"),
            "mismatches": _string_list(policy.get("mismatches")),
            "case_id": policy.get("case_id"),
            "source": policy.get("source"),
        },
    }
