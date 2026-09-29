"""Deterministic graders for manifest-backed eval cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ap_agent.core.checks import CheckLedger
from ap_agent.policy.engine import CHECK_NAMES, evaluate_all
from apfixtures.manifest import ManifestEntry
from apfixtures.spec import FailureMode
from evals.policy_context import build_policy_context_for_entry


@dataclass(slots=True)
class CaseGrade:
    case_id: str
    failure_mode: str
    passed: bool
    route_match: bool
    check_matches: int
    check_total: int
    mismatches: list[str] = field(default_factory=list)


def grade_policy_entry(entry: ManifestEntry) -> CaseGrade:
    """Grade one manifest entry against the deterministic policy engine."""
    context = build_policy_context_for_entry(entry)
    ledger: CheckLedger = evaluate_all(context)

    expected_checks = {
        c["name"]: c["verdict"]
        for c in entry.expected_checks
        if c["name"] in CHECK_NAMES
    }
    matches = 0
    mismatches: list[str] = []

    for name, expected_verdict in expected_checks.items():
        actual = ledger.by_name(name)
        if actual is None:
            mismatches.append(f"{name}: missing from ledger")
            continue
        if actual.verdict.value == expected_verdict:
            matches += 1
        else:
            mismatches.append(
                f"{name}: expected {expected_verdict}, got {actual.verdict.value}"
            )

    passed = matches == len(expected_checks) and not mismatches
    return CaseGrade(
        case_id=entry.case_id,
        failure_mode=entry.failure_mode.value
        if isinstance(entry.failure_mode, FailureMode)
        else str(entry.failure_mode),
        passed=passed,
        route_match=(len(ledger.review_forcing) > 0) == entry.requires_human_review,
        check_matches=matches,
        check_total=len(expected_checks),
        mismatches=mismatches,
    )


def aggregate_policy_grades(grades: list[CaseGrade]) -> dict[str, float | dict[str, float]]:
    if not grades:
        return {"policy_adherence": 0.0, "per_mode": {}}

    passed = sum(1 for g in grades if g.passed)
    per_mode: dict[str, list[bool]] = {}
    for g in grades:
        per_mode.setdefault(g.failure_mode, []).append(g.passed)

    return {
        "policy_adherence": round(passed / len(grades), 4),
        "per_mode": {
            mode: round(sum(1 for p in ps if p) / len(ps), 4) for mode, ps in per_mode.items()
        },
    }


def compare_extraction_fields(
    expected: dict[str, object],
    actual: dict[str, object],
    *,
    amount_tolerance: Decimal = Decimal("0.01"),
) -> tuple[int, int, list[str]]:
    """Return (matches, total, mismatches) for scalar extraction fields."""
    keys = (
        "invoice_number",
        "invoice_date",
        "vendor_name_printed",
        "currency",
        "subtotal",
        "total_amount",
        "po_reference",
    )
    matches = 0
    mismatches: list[str] = []
    for key in keys:
        exp = expected.get(key)
        act = actual.get(key)
        if exp is None and act is None:
            matches += 1
            continue
        if exp is None or act is None:
            mismatches.append(f"{key}: expected {exp!r}, got {act!r}")
            continue
        if key in ("subtotal", "total_amount"):
            try:
                if abs(Decimal(str(exp)) - Decimal(str(act))) <= amount_tolerance:
                    matches += 1
                else:
                    mismatches.append(f"{key}: expected {exp}, got {act}")
            except Exception:
                mismatches.append(f"{key}: non-numeric compare failed")
        elif str(exp).strip().lower() == str(act).strip().lower():
            matches += 1
        else:
            mismatches.append(f"{key}: expected {exp!r}, got {act!r}")
    return matches, len(keys), mismatches
