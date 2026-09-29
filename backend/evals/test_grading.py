"""Deterministic eval grading tests (no AWS)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apfixtures.manifest import load_manifest
from evals.grading import grade_policy_entry
from evals.harness import assert_gate, run_scorecard
from evals.scorecard import write_scorecard

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "datasets" / "generated" / "manifest.json"

pytestmark = pytest.mark.eval


@pytest.mark.skipif(not MANIFEST.exists(), reason="dataset not generated")
def test_policy_grade_clean_touchless_passes() -> None:
    manifest = load_manifest(MANIFEST)
    entry = next(e for e in manifest.entries if e.case_id.startswith("clean_touchless"))
    grade = grade_policy_entry(entry)
    assert grade.passed, grade.mismatches


@pytest.mark.skipif(not MANIFEST.exists(), reason="dataset not generated")
def test_scorecard_smoke_meets_gate() -> None:
    scorecard = run_scorecard(manifest_path=MANIFEST, mode="smoke", live=False)
    assert scorecard["status"] == "available"
    assert scorecard["policy_adherence"] >= 0.80
    write_scorecard(scorecard)
    assert_gate(scorecard)
