"""Eval gate — writes results.json as the CLI/CI manifest scorecard."""

from __future__ import annotations

from evals.scorecard import build_policy_scorecard


def test_policy_adherence_smoke_writes_scorecard() -> None:
    payload = build_policy_scorecard()
    assert payload["status"] == "available"
    assert payload["policy_adherence"] >= 0.80
    assert payload["metrics"]["policy_adherence"] >= 0.80
    assert payload["metrics"]["routing_label_consistency"] >= 0.70
    assert payload["metrics"]["task_completion"] is None
    assert payload["metrics"]["tool_correctness"] is None
    assert payload["metrics"]["argument_correctness"] is None
    assert payload.get("pipeline_per_mode")
