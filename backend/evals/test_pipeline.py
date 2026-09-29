"""Pipeline routing-label consistency grading against the manifest."""

from __future__ import annotations

from apfixtures.manifest import load_manifest
from evals.harness import DEFAULT_MANIFEST, _subset_manifest, run_scorecard
from evals.pipeline_grading import run_pipeline_eval


def test_routing_label_consistency_smoke_meets_threshold() -> None:
    manifest = load_manifest(DEFAULT_MANIFEST)
    entries = _subset_manifest(manifest, "smoke")
    result = run_pipeline_eval(entries)
    assert result["routing_label_consistency"] >= 0.70
    assert "tool_correctness" not in result
    assert "argument_correctness" not in result


def test_scorecard_includes_pipeline_metrics() -> None:
    payload = run_scorecard(mode="smoke", live=False)
    assert payload["status"] == "available"
    metrics = payload["metrics"]
    assert metrics["routing_label_consistency"] is not None
    assert metrics["routing_label_consistency"] >= 0.70
    assert metrics["task_completion"] is None
    # Tool metrics are never fabricated by deterministic CI; live DeepEval
    # populates them from the actual ERP trajectory.
    assert metrics["tool_correctness"] is None
    assert metrics["argument_correctness"] is None
    assert payload.get("pipeline_per_mode")
