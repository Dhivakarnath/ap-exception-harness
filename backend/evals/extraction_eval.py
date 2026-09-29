"""Extraction accuracy vs manifest ground truth, per failure mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apfixtures.manifest import ManifestEntry
from evals.live_runner import run_live_extraction_sample

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = REPO_ROOT / "datasets" / "generated"


def run_extraction_eval(
    entries: list[ManifestEntry],
    *,
    max_cases: int | None = None,
) -> dict[str, Any]:
    """Grade live extraction for each entry; aggregate overall and per mode."""
    subset = entries[:max_cases] if max_cases else entries
    per_case: list[dict[str, Any]] = []
    per_mode_scores: dict[str, list[float]] = {}

    for entry in subset:
        score, mismatches = run_live_extraction_sample(entry)
        mode_key = (
            entry.failure_mode.value
            if hasattr(entry.failure_mode, "value")
            else str(entry.failure_mode)
        )
        if score is not None:
            per_mode_scores.setdefault(mode_key, []).append(score)
        per_case.append(
            {
                "case_id": entry.case_id,
                "failure_mode": mode_key,
                "score": score,
                "mismatches": mismatches[:5],
            }
        )

    all_scores = [s for scores in per_mode_scores.values() for s in scores]
    overall = round(sum(all_scores) / len(all_scores), 4) if all_scores else None
    per_mode = {
        m: round(sum(s) / len(s), 4) for m, s in per_mode_scores.items() if s
    }
    return {
        "extraction_accuracy": overall,
        "per_mode": per_mode,
        "cases_run": len(per_case),
        "cases": per_case[:30],
    }
