"""Platform + HITL-resume + document-image API surface for the transparency UI.

These guarantee the dedicated pages have real data to render and that the HITL
review loop actually resumes a paused run (the gap INC-017 left open):

* `/policy` returns the loaded, versioned policy pack (thresholds + DOA);
* `/guardrails` returns the three real defence layers and the scope tokens;
* `/evals` returns only real live-document DeepEval records;
* `/documents/{name}/image` renders a real fixture invoice to PNG;
* a route-for-approval run pauses (`awaiting_review`) and `POST /runs/{id}/review`
  resumes it to a terminal decision with the human as approver of record.

Offline/scripted throughout — no AWS. Postgres is optional: persistence is
best-effort, so these pass whether or not a DB is reachable.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from ap_agent.api.main import app


def _drain(client: TestClient, run_id: str) -> None:
    with client.stream("GET", f"/runs/{run_id}/events") as resp:
        for _ in resp.iter_lines():
            pass


class TestPlatformEndpoints:
    def test_policy_returns_the_pack(self) -> None:
        client = TestClient(app)
        p: dict[str, Any] = client.get("/policy").json()
        assert p["identity"].startswith("retail-demo@")
        assert "touchless_max" in p["thresholds"]
        assert len(p["doa"]["bands"]) >= 1

    def test_guardrails_returns_three_layers_and_tokens(self) -> None:
        client = TestClient(app)
        g = client.get("/guardrails").json()
        assert [layer["layer"] for layer in g["layers"]] == [1, 2, 3]
        assert g["no_payment_capability"] is True
        assert "erp-reader" in g["tokens"]
        # The reader token must not grant write — the least-privilege property.
        assert "erp:write" not in g["tokens"]["erp-reader"]

    def test_evals_reports_not_run_honestly(self) -> None:
        client = TestClient(app)
        e = client.get("/evals").json()
        assert e["status"] in {"not_run", "available"}
        assert isinstance(e["evaluations"], list)
        scorecard = e.get("manifest_scorecard")
        assert scorecard is not None
        assert scorecard["status"] in {"not_run", "available"}
        if scorecard["status"] == "available":
            assert "policy_adherence" in scorecard
            assert "routing_label_consistency" in scorecard
        if e["status"] == "available":
            assert e["evaluations"]
            first = e["evaluations"][0]
            assert set(first["metrics"]) == {
                "task_completion",
                "tool_use",
                "rag_grounding",
                "extraction_accuracy",
                "policy_adherence",
            }
            assert "tools" in first["breakdown"]
            assert "rag" in first["breakdown"]

    def test_document_image_renders_a_real_fixture_png(self) -> None:
        client = TestClient(app)
        r = client.get("/documents/clean_touchless/image")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_document_image_404_for_unknown_fixture(self) -> None:
        client = TestClient(app)
        assert client.get("/documents/does_not_exist/image").status_code == 404

    def test_operations_summary_returns_dashboard_shape(self) -> None:
        client = TestClient(app)
        r = client.get("/tenants/all/operations-summary")
        assert r.status_code == 200
        body = r.json()
        assert body["tenant_id"] == "all"
        assert "kpis" in body
        assert "benchmarks" in body
        assert "recent_runs" in body
        assert "trends" in body
        assert body["trends"]["days"] == 14

    def test_observability_status_is_honest(self) -> None:
        client = TestClient(app)
        s = client.get("/platform/observability/status").json()
        assert "otel_configured" in s
        assert "deep_traces_available" in s
        assert "langfuse_url" in s


class TestHitlResume:
    def test_route_for_approval_pauses_then_resumes(self) -> None:
        client = TestClient(app)
        run_id = client.post("/runs", json={"scenario": "route_for_approval"}).json()[
            "run_id"
        ]
        _drain(client, run_id)
        # Give the finaliser a beat.
        summary = None
        for _ in range(40):
            summary = client.get(f"/runs/{run_id}").json()
            if summary.get("status") == "awaiting_review":
                break
            time.sleep(0.05)
        assert summary is not None
        assert summary["status"] == "awaiting_review"
        assert summary["pending_review"] is True

        resumed = client.post(
            f"/runs/{run_id}/review",
            json={"decision": "approve", "approver_identity": "controller@corp"},
        )
        assert resumed.status_code == 200
        # After resume the run is terminal with a human-approved route.
        final = None
        for _ in range(40):
            final = client.get(f"/runs/{run_id}").json()
            if final.get("status") == "completed":
                break
            time.sleep(0.05)
        assert final is not None
        assert final["status"] == "completed"
        assert final["route"] == "route_for_approval"

    def test_resume_unknown_run_is_conflict(self) -> None:
        client = TestClient(app)
        r = client.post(
            "/runs/not-a-real-run/review",
            json={"decision": "approve", "approver_identity": "x"},
        )
        assert r.status_code == 409
