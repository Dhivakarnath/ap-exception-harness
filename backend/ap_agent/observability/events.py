"""The normalized `RunEvent` envelope and its emitter (design §12.3).

Everything the frontend consumes crosses this one shape, so the client never
parses framework-specific payloads and a completed run *replays identically to a
live one* — the same envelope feeds the live SSE stream and the replay from
persisted rows. That single-shape guarantee is the whole point: the UI's reducer
is written once and works for both.

Two invariants the design pins down:

* **`seq` is strictly increasing per run.** The client uses it to detect a
  dropped event and refetch state (a gap in the sequence is a signal, not a
  silent loss). The `EventEmitter` owns the counter per run so ordering is
  authoritative on the backend, not reconstructed on the client.
* **PII is redacted before the envelope leaves the backend.** Tool-arg and
  result payloads pass through the same redaction the middleware applies to tool
  output, so a bank account number never reaches the stream, the browser, or a
  log — the boundary is here, not the UI's responsibility.

The emitter is deliberately transport-agnostic: it produces `RunEvent`s and
hands them to a sink callback. The SSE/WebSocket bridge is one sink; a test
collector is another; the persistence replay reads rows and re-emits through the
same construction, so nothing downstream can tell live from replayed.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ap_agent.observability.redaction import redact_pii_deep


class EventChannel(StrEnum):
    """The channels a run emits on (design §12.3).

    One per kind of thing a reviewer watches. `check` is the load-bearing one —
    every deterministic rule emits exactly one — but the whole set is what makes
    a run inspectable stage by stage rather than as a final verdict.
    """

    STAGE = "stage"
    """A graph node started/finished: name, status, duration."""
    CHECK = "check"
    """One rule evaluation: name, inputs, threshold, actual, verdict, reasoning."""
    TOOL = "tool"
    """A tool call: name, PII-redacted args, result summary."""
    EXTRACTION = "extraction"
    """Extracted canonical fields with confidence."""
    DECISION = "decision"
    """The terminal route, rationale, citations."""
    HITL = "hitl"
    """A human review: case summary, allowed decisions, channel, outcome."""
    COST = "cost"
    """Token counts and USD, cumulative per run."""
    ERROR = "error"
    """A structured failure: stage, type, message, trace id."""


class RunEvent(BaseModel):
    """One normalized event in a run's stream. The single contract the UI reads."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    trace_id: str | None = None
    seq: int = Field(ge=0, description="Monotonic per run; a gap means a dropped event.")
    channel: EventChannel
    at: datetime
    payload: dict[str, Any]

    def as_wire(self) -> dict[str, Any]:
        """The JSON shape sent over SSE/WebSocket — camel-free, ISO timestamps."""
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "seq": self.seq,
            "channel": self.channel.value,
            "at": self.at.isoformat(),
            "payload": self.payload,
        }


# A sink receives each emitted event. The bridge, a test collector, and the
# persistence writer are all sinks; the emitter does not care which.
EventSink = Callable[[RunEvent], None]


class EventEmitter:
    """Produces ordered `RunEvent`s for one run and hands them to a sink.

    Owns the per-run `seq` counter so ordering is authoritative here, not on the
    client. Every payload is passed through PII redaction before it is emitted,
    so redaction cannot be forgotten at a call site — it is the emitter's
    guarantee, not each caller's.
    """

    def __init__(
        self,
        *,
        run_id: str,
        trace_id: str | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self._run_id = run_id
        self._trace_id = trace_id
        self._sink = sink
        self._seq = itertools.count()
        self._emitted: list[RunEvent] = []

    @property
    def events(self) -> list[RunEvent]:
        """Every event emitted so far, in order — for replay and tests."""
        return list(self._emitted)

    def emit(self, channel: EventChannel, payload: dict[str, Any]) -> RunEvent:
        """Build, redact, record, and dispatch one event.

        The `seq` is assigned here (monotonic), the payload is deep-redacted of
        PII, and the event is both retained (for replay/tests) and pushed to the
        sink if one is attached. Returns the event so a caller can inspect it.
        """
        event = RunEvent(
            run_id=self._run_id,
            trace_id=self._trace_id,
            seq=next(self._seq),
            channel=channel,
            at=datetime.now(UTC),
            payload=redact_pii_deep(payload),
        )
        self._emitted.append(event)
        if self._sink is not None:
            self._sink(event)
        return event

    # -- typed convenience emitters, one per channel, so call sites are uniform

    def stage(self, *, name: str, status: str, duration_ms: float | None = None) -> RunEvent:
        return self.emit(
            EventChannel.STAGE, {"name": name, "status": status, "duration_ms": duration_ms}
        )

    def check(self, check_payload: dict[str, Any]) -> RunEvent:
        """Emit one check event. `check_payload` is `CheckResult.as_stream_payload()`."""
        return self.emit(EventChannel.CHECK, check_payload)

    def tool(self, *, name: str, args: dict[str, Any], result_summary: str | None = None) -> RunEvent:
        return self.emit(
            EventChannel.TOOL, {"name": name, "args": args, "result_summary": result_summary}
        )

    def decision(
        self,
        *,
        route: str,
        rationale: str,
        citations: list[str],
        actor: str,
        gl_account: str | None = None,
    ) -> RunEvent:
        return self.emit(
            EventChannel.DECISION,
            {
                "route": route,
                "rationale": rationale,
                "citations": citations,
                "actor": actor,
                "gl_account": gl_account,
            },
        )

    def extraction(
        self,
        *,
        fields: list[dict[str, Any]],
        line_items: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> RunEvent:
        """Emit the extracted canonical fields (name/value/confidence/region).

        A `values`-style snapshot the UI's ExtractionPanel and the document
        highlight-back consume. Each field carries its source `region_ref` so a
        click can point at the spot on the invoice it came from (FR-2.5).
        ``line_items`` (optional) carries the extracted line table — description,
        quantity, unit price, line total, unit, sku — each with its own
        confidence and region, so the UI shows the whole invoice, not only the
        header. ``meta`` (optional) carries the extraction *process* — the
        Docling parse strategy and the Bedrock model/token/image counts — so the
        UI can show how the values were read, not only what they are.
        """
        payload: dict[str, Any] = {"fields": fields}
        if line_items:
            payload["line_items"] = line_items
        if meta is not None:
            payload["meta"] = meta
        return self.emit(EventChannel.EXTRACTION, payload)

    def hitl(self, payload: dict[str, Any]) -> RunEvent:
        return self.emit(EventChannel.HITL, payload)

    def cost(self, *, input_tokens: int, output_tokens: int, usd: float) -> RunEvent:
        return self.emit(
            EventChannel.COST,
            {"input_tokens": input_tokens, "output_tokens": output_tokens, "usd": usd},
        )

    def error(self, error_payload: dict[str, Any]) -> RunEvent:
        """Emit a structured error. `error_payload` is `APAgentError.as_dict()`."""
        return self.emit(EventChannel.ERROR, error_payload)
