"""Build and persist the CLI/CI evaluation scorecard (`evals/results.json`).

The transparency UI reads live per-document DeepEval rows from Postgres via
`GET /evals`. This file remains the deterministic/live *manifest* artifact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

RESULTS_PATH = Path(__file__).resolve().parent / "results.json"


def write_scorecard(payload: dict[str, Any]) -> Path:
    payload.setdefault("generated_at", datetime.now(UTC).isoformat())
    RESULTS_PATH.write_text(json.dumps(payload, indent=2))
    return RESULTS_PATH


def build_and_write_scorecard(
    *,
    mode: Literal["smoke", "full"] = "full",
    live: bool = False,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    from evals.harness import run_scorecard

    kwargs: dict[str, Any] = {"mode": mode, "live": live}
    if manifest_path is not None:
        kwargs["manifest_path"] = manifest_path
    payload = run_scorecard(**kwargs)
    if payload.get("status") == "available":
        write_scorecard(payload)
    return payload


def build_policy_scorecard() -> dict[str, Any]:
    """Backward-compatible smoke entry point for ``make eval``."""
    return build_and_write_scorecard(mode="smoke", live=False)
