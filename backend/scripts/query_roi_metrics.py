"""Print operational KPIs and latency percentiles from persisted runs.

Usage (Postgres must be up, migrations applied):

    cd backend && uv run python scripts/query_roi_metrics.py

Outputs JSON to stdout for pasting into docs/roi-framing.md revision notes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import text

from ap_agent.observability.metrics import ap_kpis, run_metrics, summarise
from ap_agent.persistence.db import session_scope


def _latency_by_route() -> list[dict[str, object]]:
    with session_scope() as session:
        rows = session.execute(
            text(
                """
                SELECT d.route,
                       COUNT(*) AS n,
                       ROUND(AVG(r.duration_ms)::numeric, 1) AS avg_ms,
                       ROUND(
                           (PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.duration_ms))::numeric,
                           1
                       ) AS p50_ms,
                       ROUND(
                           (PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY r.duration_ms))::numeric,
                           1
                       ) AS p95_ms
                FROM runs r
                JOIN decisions d ON d.run_id = r.run_id
                WHERE r.duration_ms IS NOT NULL
                GROUP BY d.route
                ORDER BY n DESC
                """
            )
        ).fetchall()
    return [
        {
            "route": row.route,
            "n": int(row.n),
            "avg_ms": float(row.avg_ms),
            "p50_ms": float(row.p50_ms),
            "p95_ms": float(row.p95_ms),
        }
        for row in rows
    ]


def main() -> None:
    tenant = os.environ.get("ROI_METRICS_TENANT", "all")
    payload: dict[str, object] = {
        "tenant_id": tenant,
        "latency_by_route": _latency_by_route(),
    }
    with session_scope() as session:
        payload["run_metrics"] = [m.as_dict() for m in run_metrics(session, tenant_id=tenant)]
        payload["ap_kpis"] = [m.as_dict() for m in ap_kpis(session, tenant_id=tenant)]
        payload["summarise"] = summarise(session, tenant_id=tenant)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
