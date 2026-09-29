"""FastAPI SSE endpoint for the RunEvent stream (design §12.3, FR-12.3).

`GET /runs/{run_id}/events` returns a `text/event-stream`:

* if the run is **in flight** (a live emitter is registered), it streams each
  `RunEvent` as it is emitted, ending when the run completes;
* if the run is **completed** (no live emitter), it replays the persisted rows
  as the same event shape in one pass and closes.

Either way the client sees one normalized stream and its reducer folds it into
`RunState` — a completed run replays identically to a live one, which is the
whole point of the single envelope.

A KPI endpoint (`GET /tenants/{tenant_id}/kpis`) is included here too since it is
the dashboard's read side of the same observability layer.
"""

from __future__ import annotations

import queue
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from ap_agent.observability import sse
from ap_agent.observability.events import RunEvent

router = APIRouter(tags=["observability"])

# How long the live stream waits for the next event before checking whether the
# run is still active. Keeps the generator responsive to run completion.
_LIVE_POLL_SECONDS = 1.0


def _live_stream(run_id: str) -> Iterator[str]:
    """Yield SSE frames for an in-flight run by draining its emitter's events.

    A `queue.Queue` sink is attached so the emitter's push becomes this
    generator's pull. When the emitter is unregistered (run complete) and the
    queue is drained, the stream ends.
    """
    emitter = sse.get_emitter(run_id)
    if emitter is None:
        return
    q: queue.Queue[RunEvent] = queue.Queue()
    # Replay anything already emitted before the client connected, in order.
    for event in emitter.events:
        yield sse.sse_event(event)
    # Attach a sink for subsequent events. (The emitter supports one sink; in a
    # multi-subscriber deployment this becomes a fan-out — noted, not needed for
    # the single-viewer demo.)
    emitter._sink = q.put  # noqa: SLF001 - intentional live bridge
    while sse.get_emitter(run_id) is not None:
        try:
            event = q.get(timeout=_LIVE_POLL_SECONDS)
        except queue.Empty:
            continue
        yield sse.sse_event(event)
    # Drain any final events queued between the last poll and unregistration.
    while not q.empty():
        yield sse.sse_event(q.get_nowait())


@router.get("/runs/{run_id}/events", response_model=None)
def run_events(run_id: str) -> StreamingResponse | JSONResponse:
    """Stream a run's events (live) or replay them (completed)."""
    if sse.get_emitter(run_id) is not None:
        return StreamingResponse(_live_stream(run_id), media_type="text/event-stream")

    # Completed run: replay persisted rows as one SSE batch.
    from ap_agent.persistence.db import session_scope

    def _replay() -> Iterator[str]:
        with session_scope() as session:
            for event in sse.replay_events_from_db(session, run_id=run_id):
                yield sse.sse_event(event)

    return StreamingResponse(_replay(), media_type="text/event-stream")


@router.get("/tenants/{tenant_id}/kpis")
def tenant_kpis(tenant_id: str) -> JSONResponse:
    """The AP KPI + run-metric summary for a tenant's dashboard."""
    from ap_agent.observability.metrics import summarise
    from ap_agent.persistence.db import session_scope

    with session_scope() as session:
        payload: dict[str, Any] = summarise(session, tenant_id=tenant_id)
    return JSONResponse(payload)
