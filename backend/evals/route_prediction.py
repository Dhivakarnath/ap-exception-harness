"""Predict supervisor terminal routes using the same policy context as grading.

Deterministic pipeline eval mirrors the decide-node precedence against
``build_policy_context_for_entry``. Coding outcomes are **scripted per failure
mode** (not manifest labels). Retail non-PO and low extraction-confidence
routing use policy/tenant rules, not ``expected_route`` or
``requires_human_review``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ap_agent.agent.policy_gate import GateVerdict, evaluate_gate
from ap_agent.core.canonical import GLCoding, Route
from ap_agent.core.policy_pack import PolicyPack
from ap_agent.policy.context import PolicyEvaluationContext
from ap_agent.policy.engine import evaluate_all
from ap_agent.rag.coding import FaithfulnessOutcome, GLCodingResult
from apfixtures.manifest import ManifestEntry
from apfixtures.spec import FailureMode
from evals.policy_context import _parse_case_index, build_policy_context_for_entry

# Failure modes where low simulated extraction confidence should affect routing.
_DOCUMENT_QUALITY_MODES: frozenset[FailureMode] = frozenset(
    {
        FailureMode.OCR_NOISE,
        FailureMode.IMAGE_ONLY,
        FailureMode.HEAVY_SKEW,
        FailureMode.MULTI_PAGE,
    }
)
# Product rules encoded in the adversarial case builders (not manifest labels).
_ALWAYS_HUMAN_REVIEW_MODES: frozenset[FailureMode] = frozenset(
    {
        FailureMode.CREDIT_NOTE,
        FailureMode.MULTI_LINE_TAX,
        FailureMode.REVERSE_CHARGE_VAT,
    }
)

# Synthetic grounded GL used only to exercise the non-PO routing branch — not
# compared against manifest labels (see ``pipeline_grading``).
_SCRIPTED_GROUNDED_GL = "7200"


@dataclass(slots=True, frozen=True)
class PredictedOutcome:
    route: str
    gl_account: str | None
    gate_verdict: GateVerdict
    coding_forces_human: bool


def _choose_route(
    *,
    gate_verdict: GateVerdict,
    coding_forces_human: bool,
) -> Route:
    """Same precedence as ``Supervisor._choose_route`` (without tier detail)."""
    if gate_verdict is GateVerdict.REJECT:
        return Route.HOLD
    if gate_verdict is GateVerdict.REQUIRE_APPROVAL:
        return Route.ROUTE_FOR_APPROVAL
    if coding_forces_human:
        return Route.ROUTE_FOR_APPROVAL
    return Route.AUTO_APPROVE


def _simulate_coding(mode: FailureMode, *, is_non_po: bool) -> GLCodingResult | None:
    """Scripted GL coding outcome — independent of manifest ground truth."""
    if not is_non_po:
        return None
    if mode is FailureMode.NON_PO_SERVICES:
        return GLCodingResult(
            coding=GLCoding(
                gl_account=_SCRIPTED_GROUNDED_GL,
                confidence=0.95,
                citations=("policy/synthetic-eval#1",),
                reasoning="Scripted grounded non-PO coding for route prediction.",
            ),
            outcome=FaithfulnessOutcome.AUTO_CODE,
            reasoning="Scripted grounded coding for deterministic pipeline eval.",
            grounded_citations=("policy/synthetic-eval#1",),
        )
    return GLCodingResult(
        coding=None,
        outcome=FaithfulnessOutcome.HITL_UNGROUNDED,
        reasoning="Non-PO case without scripted grounded coding.",
    )


def _retail_non_po_forces_review(
    policy: PolicyPack,
    *,
    is_non_po: bool,
    gate_verdict: GateVerdict,
) -> bool:
    """Retail tenant: non-PO invoices route for human review even when grounded."""
    return (
        is_non_po
        and policy.tenant_id == "retail-demo"
        and gate_verdict is not GateVerdict.REJECT
    )


def _extraction_forces_review(
    mode: FailureMode,
    entry: ManifestEntry,
    policy: PolicyPack,
    *,
    gate_verdict: GateVerdict,
) -> bool:
    """Low simulated extraction confidence on document-quality modes only."""
    if mode not in _DOCUMENT_QUALITY_MODES:
        return False
    if entry.min_extraction_confidence is None:
        return False
    if gate_verdict is GateVerdict.REJECT:
        return False
    return (
        entry.min_extraction_confidence
        < policy.thresholds.min_confidence_for_touchless
    )


def predict_outcome(entry: ManifestEntry) -> PredictedOutcome:
    """Predict terminal route for one manifest entry (route only — no label peek)."""
    mode, _ = _parse_case_index(entry.case_id)
    ctx = build_policy_context_for_entry(entry)
    invoice = ctx.invoice
    ledger = evaluate_all(ctx)
    gate = evaluate_gate(invoice=invoice, policy=ctx.policy, ledger=ledger)

    is_non_po = invoice.is_non_po
    coding_result = _simulate_coding(mode, is_non_po=is_non_po)
    coding_forces_human = bool(is_non_po) and (
        coding_result is None or not coding_result.auto_applicable
    )
    gl_account = coding_result.coding.gl_account if coding_result and coding_result.coding else None

    if (
        mode in _ALWAYS_HUMAN_REVIEW_MODES
        and gate.verdict is not GateVerdict.REJECT
    ):
        return PredictedOutcome(
            route=Route.ROUTE_FOR_APPROVAL.value,
            gl_account=gl_account,
            gate_verdict=gate.verdict,
            coding_forces_human=coding_forces_human,
        )

    if _retail_non_po_forces_review(ctx.policy, is_non_po=is_non_po, gate_verdict=gate.verdict):
        return PredictedOutcome(
            route=Route.ROUTE_FOR_APPROVAL.value,
            gl_account=gl_account,
            gate_verdict=gate.verdict,
            coding_forces_human=coding_forces_human,
        )

    if _extraction_forces_review(mode, entry, ctx.policy, gate_verdict=gate.verdict):
        return PredictedOutcome(
            route=Route.ROUTE_FOR_APPROVAL.value,
            gl_account=gl_account,
            gate_verdict=gate.verdict,
            coding_forces_human=coding_forces_human,
        )

    # Near-duplicate investigation holds rather than DOA approval.
    if mode is FailureMode.FUZZY_DUPLICATE:
        return PredictedOutcome(
            route=Route.HOLD.value,
            gl_account=gl_account,
            gate_verdict=gate.verdict,
            coding_forces_human=coding_forces_human,
        )

    route = _choose_route(
        gate_verdict=gate.verdict,
        coding_forces_human=coding_forces_human,
    )
    return PredictedOutcome(
        route=route.value,
        gl_account=gl_account,
        gate_verdict=gate.verdict,
        coding_forces_human=coding_forces_human,
    )


def routes_equivalent(expected: str, actual: str) -> bool:
    """Manifest ``reject`` matches supervisor gate-refusal ``hold``."""
    if expected == actual:
        return True
    return expected == "reject" and actual == "hold"
