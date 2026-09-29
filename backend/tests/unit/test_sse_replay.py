"""The replay path must carry the SAME depth as the live check stream.

A completed run renders from `replay_events_from_db` (reconstructed from the
persisted rows), not from the live emitter. If replay dropped a check's
`inputs` or `duration_ms`, the transparency UI's per-check "how and why" would
be blank for every finished run — visible live, gone on reload. This locks the
replayed check payload to the full shape the UI's `CheckPayload` expects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from ap_agent.observability.sse import replay_events_from_db


class _Query:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def filter_by(self, **_: Any) -> _Query:
        return self

    def order_by(self, *_: Any) -> _Query:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """The narrow surface `replay_events_from_db` uses: get() + query()."""

    def __init__(
        self, run: Any, checks: list[Any], invoice: Any = None
    ) -> None:
        self._run = run
        self._checks = checks
        self._invoice = invoice

    def get(self, model: Any, _id: str) -> Any:
        if model.__name__ == "Run":
            return self._run
        if model.__name__ == "Invoice":
            return self._invoice
        return None

    def query(self, model: Any) -> _Query:
        if model.__name__ == "CheckResultRow":
            return _Query(self._checks)
        return _Query([])  # decisions: none


def _run(run_id: str) -> Any:
    now = datetime.now(UTC)
    return SimpleNamespace(
        run_id=run_id,
        invoice_id=f"inv-{run_id}",
        trace_id="trace-1",
        status="completed",
        started_at=now,
        finished_at=now,
        duration_ms=51.0,
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.0001,
    )


def _invoice_with_provenance() -> Any:
    """An Invoice row carrying the field_provenance JSONB an upload writes."""
    return SimpleNamespace(
        field_provenance={
            "process": {
                "parse_strategy": "structural",
                "escalated_to_ocr": False,
                "parser_name": "docling",
                "model_id": "amazon.nova-lite-v1:0",
                "images_attached": 0,
                "attempts_used": 1,
                "input_tokens": 7940,
                "output_tokens": 459,
            },
            "fields": {
                "invoice_number": {
                    "value": "INV-60000",
                    "confidence": 0.98,
                    "method": "parsed_structure",
                    "source_label": "Invoice Number",
                    "element_ref": "#/texts/9",
                    "page": 1,
                    "bbox": {"left": 0.7, "top": 0.1, "right": 0.9, "bottom": 0.12},
                },
                "total_amount": {
                    "value": "103.92 USD",
                    "confidence": 0.91,
                    "method": "parsed_structure",
                    "source_label": "Total",
                    "element_ref": "#/texts/15",
                    "page": 1,
                    "bbox": None,
                },
            },
            "line_items": [
                {
                    "line_number": 1,
                    "description": "Steel bracket, 40mm",
                    "quantity": "10",
                    "unit_price": "12.00 USD",
                    "line_total": "120.00 USD",
                    "unit_of_measure": "EA",
                    "sku": None,
                    "confidence": 0.95,
                    "region_ref": "#/texts/20",
                    "page": 1,
                    "bbox": {"left": 0.1, "top": 0.4, "right": 0.9, "bottom": 0.42},
                }
            ],
        }
    )


def _check_row() -> Any:
    return SimpleNamespace(
        name="math_integrity",
        category="arithmetic",
        verdict="pass",
        severity="info",
        reasoning="Line items sum to the printed subtotal.",
        threshold="exact match within 5.00",
        actual="reconciles",
        inputs={"printed_subtotal": "500.00 USD", "computed_line_sum": "500.00 USD"},
        forces_review=False,
        citations=[],
        duration_ms=1.4,
        evaluated_at=datetime.now(UTC),
        sequence=1,
    )


def test_replayed_check_carries_inputs_and_duration() -> None:
    session = _FakeSession(_run("run-x"), [_check_row()])
    events = replay_events_from_db(session, run_id="run-x")

    checks = [e for e in events if e["channel"] == "check"]
    assert len(checks) == 1
    payload = checks[0]["payload"]

    # The exact intermediate values the check used — the "how" the UI expands.
    assert payload["inputs"] == {
        "printed_subtotal": "500.00 USD",
        "computed_line_sum": "500.00 USD",
    }
    assert payload["duration_ms"] == 1.4
    # And the shape stays complete.
    for key in ("name", "category", "verdict", "severity", "reasoning",
                "threshold", "actual", "forces_review", "citations"):
        assert key in payload


def test_replayed_check_tolerates_null_inputs() -> None:
    row = _check_row()
    row.inputs = None
    row.duration_ms = None
    session = _FakeSession(_run("run-y"), [row])

    payload = next(
        e["payload"]
        for e in replay_events_from_db(session, run_id="run-y")
        if e["channel"] == "check"
    )
    # A row with no recorded inputs replays as an empty object, not a crash.
    assert payload["inputs"] == {}
    assert payload["duration_ms"] is None


def test_replay_reconstructs_the_extraction_event_from_provenance() -> None:
    # A run whose Invoice carries field_provenance must re-emit the extraction
    # event on replay — the fix for "No fields yet" on reload.
    session = _FakeSession(
        _run("run-z"), [_check_row()], invoice=_invoice_with_provenance()
    )
    events = replay_events_from_db(session, run_id="run-z")

    extraction = [e for e in events if e["channel"] == "extraction"]
    assert len(extraction) == 1, "replay must emit exactly one extraction event"
    payload = extraction[0]["payload"]

    # The extraction event sits before the checks (live order).
    channels = [e["channel"] for e in events]
    assert channels.index("extraction") < channels.index("check")

    # Fields reproduce the exact live shape, with confidence + region + bbox.
    by_name = {f["name"]: f for f in payload["fields"]}
    assert by_name["invoice_number"]["value"] == "INV-60000"
    assert by_name["invoice_number"]["confidence"] == 0.98
    assert by_name["invoice_number"]["region_ref"] == "#/texts/9"
    assert by_name["invoice_number"]["bbox"] == {
        "left": 0.7,
        "top": 0.1,
        "right": 0.9,
        "bottom": 0.12,
    }
    # A field with no recovered box replays bbox=None, never a fabricated one.
    assert by_name["total_amount"]["bbox"] is None

    # The extracted line table survives replay too, with its per-line detail.
    line_items = payload["line_items"]
    assert len(line_items) == 1
    line = line_items[0]
    assert line["description"] == "Steel bracket, 40mm"
    assert line["quantity"] == "10"
    assert line["unit_of_measure"] == "EA"
    assert line["line_total"] == "120.00 USD"
    assert line["region_ref"] == "#/texts/20"

    # The Docling->Bedrock process metadata survives too.
    assert payload["meta"]["parse_strategy"] == "structural"
    assert payload["meta"]["model_id"] == "amazon.nova-lite-v1:0"
    assert payload["meta"]["input_tokens"] == 7940


def test_replay_emits_no_extraction_when_no_provenance() -> None:
    # The state-first demo path never extracts, so its Invoice has no
    # field_provenance — replay must NOT fabricate an extraction event.
    session = _FakeSession(_run("run-w"), [_check_row()], invoice=None)
    events = replay_events_from_db(session, run_id="run-w")
    assert not [e for e in events if e["channel"] == "extraction"]

    # An invoice row present but with null provenance is also honestly empty.
    bare = SimpleNamespace(field_provenance=None)
    session2 = _FakeSession(_run("run-v"), [_check_row()], invoice=bare)
    events2 = replay_events_from_db(session2, run_id="run-v")
    assert not [e for e in events2 if e["channel"] == "extraction"]
