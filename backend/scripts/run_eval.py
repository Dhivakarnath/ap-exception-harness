"""Run the Slice 13 eval harness and print the scorecard.

    uv run python scripts/run_eval.py              # deterministic policy (full manifest)
    uv run python scripts/run_eval.py --smoke      # one case per failure mode
    uv run python scripts/run_eval.py --live       # + DeepEval RAG triad + extraction sample
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from evals.harness import assert_gate  # noqa: E402
from evals.scorecard import build_and_write_scorecard  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="One case per failure mode.")
    parser.add_argument("--live", action="store_true", help="Include live Bedrock/DeepEval tiers.")
    parser.add_argument("--no-gate", action="store_true", help="Skip CI threshold assertion.")
    args = parser.parse_args(argv)

    mode = "smoke" if args.smoke else "full"
    scorecard = build_and_write_scorecard(mode=mode, live=args.live)
    print(json.dumps(scorecard, indent=2))

    if scorecard.get("status") != "available":
        print(scorecard.get("message", "eval failed"), file=sys.stderr)
        return 1

    if not args.no_gate:
        try:
            assert_gate(scorecard, deterministic_only=args.live)
        except AssertionError as exc:
            print(f"CI gate failed: {exc}", file=sys.stderr)
            return 1

    print("\nEval gate passed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
