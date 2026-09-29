"""End-to-end pipeline grading — deterministic route prediction + optional live runs."""

from __future__ import annotations

from dataclasses import dataclass, field

from ap_agent.core.canonical import Route
from apfixtures.manifest import ManifestEntry
from apfixtures.spec import FailureMode
from evals.route_prediction import predict_outcome, routes_equivalent


@dataclass(slots=True)
class PipelineGrade:
    case_id: str
    failure_mode: str
    passed: bool
    expected_route: str
    actual_route: str | None
    pending_hitl: bool
    mismatches: list[str] = field(default_factory=list)


def grade_pipeline_entry(entry: ManifestEntry) -> PipelineGrade:
    """Grade one manifest entry against predicted supervisor routing (route only)."""
    predicted = predict_outcome(entry)
    actual = predicted.route
    expected = entry.expected_route
    mismatches: list[str] = []

    route_ok = routes_equivalent(expected, actual)
    if not route_ok:
        mismatches.append(f"route: expected {expected}, got {actual}")

    mode_key = (
        entry.failure_mode.value
        if isinstance(entry.failure_mode, FailureMode)
        else str(entry.failure_mode)
    )
    return PipelineGrade(
        case_id=entry.case_id,
        failure_mode=mode_key,
        passed=route_ok and not mismatches,
        expected_route=expected,
        actual_route=actual,
        pending_hitl=actual == Route.ROUTE_FOR_APPROVAL.value,
        mismatches=mismatches,
    )


def aggregate_pipeline_grades(grades: list[PipelineGrade]) -> dict[str, float | dict[str, float]]:
    if not grades:
        return {"routing_label_consistency": 0.0, "per_mode": {}}
    passed = sum(1 for g in grades if g.passed)
    per_mode: dict[str, list[bool]] = {}
    for g in grades:
        per_mode.setdefault(g.failure_mode, []).append(g.passed)
    return {
        "routing_label_consistency": round(passed / len(grades), 4),
        "per_mode": {m: round(sum(1 for p in ps if p) / len(ps), 4) for m, ps in per_mode.items()},
    }


def run_pipeline_eval(
    entries: list[ManifestEntry],
    *,
    live: bool = False,
) -> dict[str, object]:
    """Grade pipeline routing for manifest entries.

    ``live=True`` is reserved for future live-supervisor sampling; deterministic
    CI always uses policy-context route prediction (see ``route_prediction``).
    """
    del live  # live supervisor E2E is scored in ``agent_eval`` (DeepEval tier)
    grades = [grade_pipeline_entry(entry) for entry in entries]
    agg = aggregate_pipeline_grades(grades)
    failures = [g for g in grades if not g.passed]
    return {
        **agg,
        "cases_total": len(grades),
        "cases_passed": sum(1 for g in grades if g.passed),
        "failures": [{"case_id": f.case_id, "mismatches": f.mismatches[:5]} for f in failures[:20]],
    }
