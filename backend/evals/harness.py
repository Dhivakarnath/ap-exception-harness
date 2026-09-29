"""Full eval scorecard builder (Slice 13)."""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from apfixtures.manifest import DatasetManifest, load_manifest
from evals.agent_eval import run_live_agent_metrics
from evals.extraction_eval import run_extraction_eval
from evals.grading import aggregate_policy_grades, grade_policy_entry
from evals.live_runner import pick_live_sample_entries
from evals.pipeline_grading import run_pipeline_eval
from evals.thresholds import DEFAULT_THRESHOLDS, EvalThresholds

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "datasets" / "generated" / "manifest.json"

ALL_METRIC_KEYS = (
    "routing_label_consistency",
    "task_completion",
    "tool_correctness",
    "argument_correctness",
    "policy_adherence",
    "rag_faithfulness",
    "rag_answer_relevancy",
    "rag_contextual_relevancy",
    "extraction_accuracy",
)


def _metrics_payload(values: dict[str, float | None]) -> dict[str, float | None]:
    return {k: values.get(k) for k in ALL_METRIC_KEYS}


def _subset_manifest(manifest: DatasetManifest, mode: Literal["smoke", "full"]) -> list[Any]:
    entries = manifest.entries
    if mode != "smoke":
        return list(entries)
    seen: set[str] = set()
    smoke: list[Any] = []
    for e in entries:
        mode_key = e.failure_mode.value if hasattr(e.failure_mode, "value") else str(e.failure_mode)
        if mode_key in seen:
            continue
        seen.add(mode_key)
        smoke.append(e)
    return smoke


def run_policy_eval(manifest: DatasetManifest) -> dict[str, Any]:
    grades = [grade_policy_entry(e) for e in manifest.entries]
    agg = aggregate_policy_grades(grades)
    failures = [g for g in grades if not g.passed]
    return {
        **agg,
        "cases_total": len(grades),
        "cases_passed": sum(1 for g in grades if g.passed),
        "failures": [{"case_id": f.case_id, "mismatches": f.mismatches[:5]} for f in failures[:20]],
    }


def run_scorecard(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    mode: Literal["smoke", "full"] = "full",
    live: bool = False,
    extraction_limit: int | None = 5,
) -> dict[str, Any]:
    """Build the scorecard dict persisted to ``results.json``."""
    if not manifest_path.exists():
        return {
            "status": "not_run",
            "message": f"Manifest not found at {manifest_path}. Run `make dataset-quick` first.",
        }

    manifest = load_manifest(manifest_path)
    entries = _subset_manifest(manifest, mode)
    subset = manifest.model_copy(update={"entries": entries})

    policy = run_policy_eval(subset)
    policy_score = float(policy["policy_adherence"])

    pipeline = run_pipeline_eval(entries, live=False)
    pipeline_routing = pipeline["routing_label_consistency"]
    if not isinstance(pipeline_routing, int | float):
        raise TypeError("pipeline routing_label_consistency must be numeric")
    routing_score = float(pipeline_routing)
    task_score: float | None = None
    tool_score: float | None = None
    arg_score: float | None = None

    extraction_score: float | None = None
    extraction_per_mode: dict[str, float] = {}
    rag_scores: tuple[float | None, float | None, float | None] = (
        None,
        None,
        None,
    )
    live_note = ""

    aws_ready = bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))
    if live and aws_ready:
        try:
            ext = run_extraction_eval(entries, max_cases=extraction_limit)
            extraction_score = ext.get("extraction_accuracy")
            extraction_per_mode = ext.get("per_mode", {})
            live_note += f" extraction_cases={ext.get('cases_run', 0)};"
        except Exception as exc:  # noqa: BLE001
            live_note += f" extraction_error={exc};"

        try:
            sample = pick_live_sample_entries(manifest_path, per_mode=1)
            if sample:
                agent = run_live_agent_metrics(sample)
                rag_scores = (
                    agent.rag_faithfulness,
                    agent.rag_answer_relevancy,
                    agent.rag_contextual_relevancy,
                )
                if agent.task_completion is not None:
                    task_score = agent.task_completion
                tool_score = agent.tool_correctness
                arg_score = agent.argument_correctness
                live_note += f" agent_cases={agent.cases_run};"
        except Exception as exc:  # noqa: BLE001
            live_note += f" live_error={exc};"
    elif live and not aws_ready:
        live_note = " live skipped: export AWS credentials in shell."

    faith, ans, ctx = rag_scores
    metrics = _metrics_payload(
        {
            "routing_label_consistency": routing_score,
            "task_completion": task_score,
            "tool_correctness": tool_score,
            "argument_correctness": arg_score,
            "policy_adherence": policy_score,
            "rag_faithfulness": faith,
            "rag_answer_relevancy": ans,
            "rag_contextual_relevancy": ctx,
            "extraction_accuracy": extraction_score,
        }
    )

    return {
        "status": "available",
        "suite": f"slice13_{mode}{'_live' if live and aws_ready else ''}",
        "policy_adherence": policy_score,
        "per_mode": policy.get("per_mode", {}),
        "pipeline_per_mode": pipeline.get("per_mode", {}),
        "extraction_per_mode": extraction_per_mode,
        "cases_total": policy.get("cases_total", 0),
        "cases_passed": policy.get("cases_passed", 0),
        "routing_label_consistency": routing_score,
        "pipeline_cases_total": pipeline.get("cases_total", 0),
        "pipeline_cases_passed": pipeline.get("cases_passed", 0),
        "failures": policy.get("failures", []),
        "pipeline_failures": pipeline.get("failures", []),
        "metrics": metrics,
        "thresholds": asdict(DEFAULT_THRESHOLDS),
        "note": (
            "Policy + route-prediction (no manifest label peek) always run. "
            "task_completion is live DeepEval only. Live tiers need shell-exported AWS creds."
            + live_note
        ),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def assert_gate(
    scorecard: dict[str, Any],
    thresholds: EvalThresholds = DEFAULT_THRESHOLDS,
    *,
    deterministic_only: bool = False,
) -> None:
    """Raise AssertionError when metrics regress (CI gate)."""
    if scorecard.get("status") != "available":
        raise AssertionError(scorecard.get("message", "eval not run"))

    metrics = scorecard.get("metrics") or {}
    checks = (
        ("policy_adherence", thresholds.policy_adherence),
        ("routing_label_consistency", thresholds.routing_label_consistency),
    )
    if not deterministic_only:
        checks += (
            ("task_completion", thresholds.task_completion),
            ("tool_correctness", thresholds.tool_correctness),
            ("argument_correctness", thresholds.argument_correctness),
            ("extraction_accuracy", thresholds.extraction_accuracy),
            ("rag_faithfulness", thresholds.rag_faithfulness),
            ("rag_answer_relevancy", thresholds.rag_answer_relevancy),
            ("rag_contextual_relevancy", thresholds.rag_contextual_relevancy),
        )
    for key, threshold in checks:
        value = metrics.get(key)
        if value is not None and value < threshold:
            raise AssertionError(f"{key} {value} < threshold {threshold}")
