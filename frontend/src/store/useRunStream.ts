/**
 * useRunStream — subscribe a component to a run's live event stream and get back
 * the folded `RunState` plus the connection state.
 *
 * Wiring: `openRunStream` (transport) delivers in-order events; each is folded
 * with `reduceEvent`. On a seq gap the transport calls `onGap`, and we reconcile
 * by fetching the full snapshot (`fetchRunEventsSnapshot`) and rebuilding state
 * with `reduceAll` — the design's "a gap means refetch" rule, so a dropped event
 * can never leave the UI silently wrong.
 */

import { useEffect, useReducer, useRef, useState } from "react";
import { fetchRunEventsSnapshot } from "@/transport/api";
import {
  type ConnectionState,
  openRunStream,
} from "@/transport/stream";
import type { RunEvent } from "@/types/events";
import {
  type RunState,
  emptyRunState,
  reduceAll,
  reduceEvent,
} from "@/store/runState";

type Action =
  | { kind: "event"; event: RunEvent }
  | { kind: "replace"; state: RunState };

function reducer(state: RunState, action: Action): RunState {
  switch (action.kind) {
    case "event":
      return reduceEvent(state, action.event);
    case "replace":
      return action.state;
  }
}

export interface UseRunStream {
  state: RunState;
  connection: ConnectionState;
}

export function useRunStream(
  runId: string | null,
  refreshNonce = 0,
): UseRunStream {
  const [state, dispatch] = useReducer(
    reducer,
    runId,
    (id) => emptyRunState(id),
  );
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  // Guards a reconcile so overlapping gaps don't trigger a fetch storm.
  const reconciling = useRef(false);

  useEffect(() => {
    if (!runId) {
      setConnection("closed");
      return;
    }
    // Reset to a fresh state when the run changes.
    dispatch({ kind: "replace", state: emptyRunState(runId) });

    let cancelled = false;

    const reconcile = async () => {
      if (reconciling.current) return;
      reconciling.current = true;
      try {
        const events = await fetchRunEventsSnapshot(runId);
        if (!cancelled && events.length > 0) {
          dispatch({ kind: "replace", state: reduceAll(events, runId) });
        }
      } catch {
        // A failed reconcile leaves the current state; the live stream continues
        // and a later gap will retry. Never crash the view over a refetch.
      } finally {
        reconciling.current = false;
      }
    };

    const stream = openRunStream(runId, {
      onEvent: (event) => {
        if (!cancelled) dispatch({ kind: "event", event });
      },
      onGap: () => {
        void reconcile();
      },
      onState: (s) => {
        if (!cancelled) setConnection(s);
      },
    });

    return () => {
      cancelled = true;
      stream.close();
    };
    // `refreshNonce` re-subscribes on demand — e.g. after a HITL resume drives
    // the run to a new terminal state out-of-band, so the view refolds from the
    // freshly persisted events.
  }, [runId, refreshNonce]);

  return { state, connection };
}
