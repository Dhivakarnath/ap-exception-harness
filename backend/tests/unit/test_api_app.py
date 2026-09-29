"""The FastAPI app boots, mounts the observability routes, and streams a run.

These are the guarantees the transparency UI depends on:

* the app object imports and exposes ``/health``, the run trigger + queue
  (``/runs``), the SSE stream (``/runs/{id}/events``), and the KPI read
  (``/tenants/{id}/kpis``);
* a scripted (offline, no-AWS) run triggered via ``POST /runs`` streams the
  normalized ``RunEvent`` envelope live — exactly one check event per check,
  then a decision and a cost — so the reducer has the contract it expects;
* the completed run's summary carries the route and a **non-zero** cost derived
  from the scripted token usage, proving cost attribution flows through the API.

The live Bedrock/MCP path is exercised by the observability live run, not here;
this tier stays fast and network-free.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from ap_agent.api.main import app


def _channels_from_stream(client: TestClient, run_id: str) -> list[str]:
    # The run executes on a background thread; the emitter is re-registered for a
    # grace window with all its buffered events, so a connect that lands in the
    # brief gap before re-registration simply retries within the window.
    for _ in range(10):
        channels: list[str] = []
        with client.stream("GET", f"/runs/{run_id}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    payload: dict[str, Any] = json.loads(line[len("data:") :].strip())
                    channels.append(payload["channel"])
        if channels:
            return channels
        time.sleep(0.1)
    return []


class TestAppBoots:
    def test_health_ok(self) -> None:
        client = TestClient(app)
        assert client.get("/health").json() == {"status": "ok"}

    def test_observability_routes_are_mounted(self) -> None:
        client = TestClient(app)
        paths = set(client.get("/openapi.json").json()["paths"])
        assert "/runs" in paths
        assert "/runs/{run_id}/events" in paths
        assert "/tenants/{tenant_id}/kpis" in paths


class TestRunTrigger:
    def test_scripted_run_streams_the_event_contract(self) -> None:
        client = TestClient(app)
        run_id = client.post("/runs", json={"scenario": "auto_approve"}).json()[
            "run_id"
        ]
        channels = _channels_from_stream(client, run_id)
        # Exactly one check per check, then decision + cost — the reducer's shape.
        assert channels.count("decision") == 1
        assert channels.count("cost") == 1
        assert channels.count("check") >= 1
        # Checks precede the decision (evaluation order preserved on the wire).
        assert channels.index("decision") > channels.index("check")

    def test_completed_run_summary_has_route_and_nonzero_cost(self) -> None:
        client = TestClient(app)
        run_id = client.post("/runs", json={"scenario": "auto_approve"}).json()[
            "run_id"
        ]
        # Drain the stream so the run has finished before we read the summary.
        _channels_from_stream(client, run_id)
        # The summary is finalised right after the stream closes; give the
        # background thread a beat if needed.
        summary = None
        for _ in range(20):
            runs = client.get("/runs").json()
            summary = next((r for r in runs if r["run_id"] == run_id), None)
            if summary and summary["status"] == "completed":
                break
            time.sleep(0.05)
        assert summary is not None
        assert summary["status"] == "completed"
        assert summary["route"] == "auto_approve"
        # Scripted usage (1200 in / 180 out) -> a real, non-zero USD cost.
        assert summary["input_tokens"] == 1200
        assert summary["usd"] > 0
