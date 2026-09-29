"""`POST /runs/upload` end to end, against a real Docling parse and a real
Bedrock extraction call.

This is the "run my own document" path: unlike every demo trigger, it starts
from actual file bytes with no pre-built `Invoice`, so the extract node must
genuinely call the model. It costs a small, real Bedrock invocation each run —
that is the point of the test, and why it is marked `bedrock` in addition to
`integration` (it also needs Postgres, since the ingress gateway persists a
`Document` row before parsing runs).

Needs Postgres + live AWS Bedrock credentials with access to the configured
extraction model.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ap_agent.api.main import _EMITTER_GRACE_SECONDS, app
from ap_agent.persistence.db import session_scope

pytestmark = [pytest.mark.integration, pytest.mark.bedrock]

_INVOICES = Path(__file__).resolve().parents[3] / "datasets" / "generated" / "invoices"

# `bank_detail_change-*` reliably extracts to completion (its line quantities
# are plain decimals). The `clean_touchless` fixtures carry a "20 EA" quantity
# that intermittently trips the no-coercion parser — a real, correct fail-loud,
# but a poor choice for a happy-path assertion; it is used only by the
# duplicate-rejection test, which does not need the run to complete. Each
# extraction-running test gets its OWN fixture so content-hash dedupe never
# makes one test's upload collide with another's in the same suite run.
_FIXTURE_HAPPY = _INVOICES / "bank_detail_change-000.pdf"
_FIXTURE_REPLAY = _INVOICES / "bank_detail_change-001.pdf"
_FIXTURE_IMAGE = _INVOICES / "bank_detail_change-002.pdf"
_FIXTURE_DUP = _INVOICES / "clean_touchless-000.pdf"
_ALL_FIXTURES = (_FIXTURE_HAPPY, _FIXTURE_REPLAY, _FIXTURE_IMAGE, _FIXTURE_DUP)
_TENANT = "retail-demo"


@pytest.fixture(autouse=True)
def _clean_fixture_documents() -> Iterator[None]:
    """Delete the test fixtures' `Document` rows before and after each test.

    Ingest dedupes on `(tenant_id, content_hash)`, which is exactly what the
    duplicate-rejection test exercises — so each test must start from a clean
    slate rather than tripping over a previous test's upload of the same bytes.
    Covers every fixture any test in this module uploads. Deleted via raw SQL
    (not `session.delete(Document(...))`): the `Document.invoices` relationship
    has no ORM cascade, so an ORM-level delete tries to null `Invoice.document_id`
    first and fails its NOT NULL constraint. The DB's own `ON DELETE CASCADE`
    (declared on every child FK down to `run_errors`/`decisions`) handles the
    whole chain correctly when the delete happens in SQL.
    """
    digests = [hashlib.sha256(f.read_bytes()).hexdigest() for f in _ALL_FIXTURES]

    def _delete() -> None:
        with session_scope() as session:
            session.execute(
                text(
                    "DELETE FROM documents WHERE tenant_id = :tenant "
                    "AND content_hash = ANY(:digests)"
                ),
                {"tenant": _TENANT, "digests": digests},
            )

    _delete()
    yield
    _delete()


def _drain(client: TestClient, run_id: str) -> list[dict[str, Any]]:
    """Collect every event from the run's stream (live or replay, same shape)."""
    import json

    events: list[dict[str, Any]] = []
    with client.stream("GET", f"/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            text = line.strip()
            if text.startswith("data:"):
                events.append(json.loads(text[len("data:") :].strip()))
    return events


def _wait_for_terminal(client: TestClient, run_id: str, *, timeout_s: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    summary: dict[str, Any] = {}
    while time.monotonic() < deadline:
        summary = client.get(f"/runs/{run_id}").json()
        if summary.get("status") in {"completed", "failed", "awaiting_review"}:
            return summary
        time.sleep(0.25)
    raise TimeoutError(f"run {run_id} did not reach a terminal state: {summary}")


class TestUploadLive:
    def test_a_real_fixture_pdf_runs_through_live_extraction(self) -> None:
        client = TestClient(app)
        with _FIXTURE_HAPPY.open("rb") as fh:
            r = client.post(
                "/runs/upload",
                files={"file": (_FIXTURE_HAPPY.name, fh, "application/pdf")},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        run_id = body["run_id"]
        assert body["stream_url"] == f"/runs/{run_id}/events"

        _drain(client, run_id)
        summary = _wait_for_terminal(client, run_id)

        # The run's own bookkeeping: a real invoice was extracted (vendor/total
        # backfilled from it, not the "—" placeholder), and real tokens were
        # spent — the signature of an actual model call, not a scripted stub.
        assert summary["status"] in {"completed", "awaiting_review"}
        assert summary["total"] != "—"
        assert summary["input_tokens"] > 0
        assert summary["usd"] > 0

        # The check ledger ran for real: at least the always-applicable
        # document/arithmetic checks are present.
        events = _drain(client, run_id)
        checks = [e for e in events if e["channel"] == "check"]
        assert len(checks) >= 5
        names = {c["payload"]["name"] for c in checks}
        assert "completeness" in names
        assert "math_integrity" in names

    def test_extraction_persists_and_replays_after_the_run(self) -> None:
        # The reload bug: extraction streamed live but vanished on replay
        # because it was never persisted/replayed. This asserts the fix — a
        # completed run's replay carries the extraction fields (with real
        # confidence + region) AND the Docling->Bedrock process metadata.
        client = TestClient(app)
        with _FIXTURE_REPLAY.open("rb") as fh:
            r = client.post(
                "/runs/upload",
                files={"file": (_FIXTURE_REPLAY.name, fh, "application/pdf")},
            )
        run_id = r.json()["run_id"]
        _drain(client, run_id)
        _wait_for_terminal(client, run_id)

        # Force the DB-replay path: wait past the emitter grace window so the
        # live emitter is unregistered and /events reconstructs from rows.
        time.sleep(_EMITTER_GRACE_SECONDS + 2.0)
        events = _drain(client, run_id)

        extraction = [e for e in events if e["channel"] == "extraction"]
        assert len(extraction) == 1, "extraction must survive replay, not vanish"
        payload = extraction[0]["payload"]

        fields = payload["fields"]
        assert len(fields) >= 4, "the real invoice's header fields should replay"
        # Each field carries a real (sub-1.0 or exactly-1.0) confidence, not a
        # missing value.
        assert all(f.get("confidence") is not None for f in fields)

        # The extracted line table survived persistence + replay too.
        line_items = payload.get("line_items", [])
        assert len(line_items) >= 1, "the real invoice's line items should replay"
        first = line_items[0]
        for key in ("description", "quantity", "unit_price", "line_total"):
            assert first.get(key), f"line item missing {key} on replay"

        # The process story is present and real: a Docling strategy and the
        # Bedrock model that actually ran.
        meta = payload["meta"]
        assert meta["parse_strategy"] in {"structural", "ocr"}
        assert meta["model_id"]
        assert meta["input_tokens"] > 0

    def test_the_uploaded_page_image_renders_after_the_run(self) -> None:
        client = TestClient(app)
        with _FIXTURE_IMAGE.open("rb") as fh:
            r = client.post(
                "/runs/upload",
                files={"file": (_FIXTURE_IMAGE.name, fh, "application/pdf")},
            )
        run_id = r.json()["run_id"]
        _drain(client, run_id)
        _wait_for_terminal(client, run_id)

        img = client.get(f"/runs/{run_id}/document/image")
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/png"
        assert img.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_duplicate_upload_of_the_same_bytes_is_rejected(self) -> None:
        # Uses the clean_touchless fixture: dedupe happens at ingress, before
        # extraction, so this test doesn't need the run to complete — only that
        # the same bytes are refused the second time.
        client = TestClient(app)
        with _FIXTURE_DUP.open("rb") as fh:
            first = client.post(
                "/runs/upload",
                files={"file": (_FIXTURE_DUP.name, fh, "application/pdf")},
            )
        assert first.status_code == 200
        _drain(client, first.json()["run_id"])
        _wait_for_terminal(client, first.json()["run_id"])

        with _FIXTURE_DUP.open("rb") as fh:
            second = client.post(
                "/runs/upload",
                files={"file": (_FIXTURE_DUP.name, fh, "application/pdf")},
            )
        assert second.status_code == 409
        assert second.json()["rejection"] == "duplicate_content"

    def test_duplicate_upload_is_allowed_when_requested(self) -> None:
        client = TestClient(app)
        with _FIXTURE_DUP.open("rb") as fh:
            first = client.post(
                "/runs/upload",
                files={"file": (_FIXTURE_DUP.name, fh, "application/pdf")},
            )
        assert first.status_code == 200
        _drain(client, first.json()["run_id"])
        _wait_for_terminal(client, first.json()["run_id"])

        with _FIXTURE_DUP.open("rb") as fh:
            second = client.post(
                "/runs/upload",
                files={"file": (_FIXTURE_DUP.name, fh, "application/pdf")},
                data={"allow_duplicate": "true"},
            )
        assert second.status_code == 200
        assert second.json()["run_id"] != first.json()["run_id"]
