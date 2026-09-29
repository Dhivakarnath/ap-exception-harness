#!/usr/bin/env python3
"""Run HITL lifecycle maintenance (expire stale reviews + sweep checkpoints).

Intended for cron or a compose sidecar in production:

    cd backend && uv run python scripts/hitl_lifecycle.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a script from repo root or backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ap_agent.api.lifecycle import run_hitl_lifecycle


def main() -> None:
    result = run_hitl_lifecycle()
    print(f"expired={result['expired']} swept={result['swept']}")


if __name__ == "__main__":
    main()
