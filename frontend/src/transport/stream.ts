/**
 * SSE transport for a run's event stream (design §12.0a / §12.3).
 *
 * Wraps the browser `EventSource` with the guarantees the reducer needs:
 *
 * - **Ordering + gap detection.** Every RunEvent carries a monotonic `seq`. The
 *   transport tracks the last seq it delivered and, if the next one skips ahead,
 *   fires `onGap` so the store can refetch the full snapshot and reconcile —
 *   the design's "a gap means a dropped event, refetch state" rule. Duplicate or
 *   out-of-order-low events (which a reconnect replay can produce) are dropped.
 *
 * - **Reconnect that knows when to stop.** `EventSource` reconnects on any
 *   connection drop, which is what we want for a transient blip. But our backend
 *   *closes the stream cleanly* when the run completes, and the browser reports
 *   that clean close as an `error` too. So once a terminal event (decision,
 *   error, or a cost following a decision) has been seen, we close ourselves and
 *   do not treat the subsequent close as a failure.
 *
 * The transport is framework-agnostic; the React store subscribes to it.
 */

import type { RunEvent } from "@/types/events";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export type ConnectionState = "connecting" | "open" | "closed" | "error";

export interface RunStreamHandlers {
  /** Each in-order event, exactly once. */
  onEvent: (event: RunEvent) => void;
  /** A seq gap was detected (expected `expected`, got `got`); refetch state. */
  onGap?: (expected: number, got: number) => void;
  /** Connection state changes, for the UI's live/closed indicator. */
  onState?: (state: ConnectionState) => void;
  /** The run reached a terminal event and the stream ended normally. */
  onComplete?: () => void;
}

export interface RunStream {
  /** Stop listening and close the connection. Idempotent. */
  close: () => void;
}

const TERMINAL_CHANNELS = new Set(["decision", "error"]);

/**
 * Open a live stream for `runId`. Returns a handle whose `close()` tears it
 * down. Safe to call from a React effect (return `stream.close` in cleanup).
 */
export function openRunStream(
  runId: string,
  handlers: RunStreamHandlers,
): RunStream {
  const url = `${API_BASE}/runs/${encodeURIComponent(runId)}/events`;
  let source: EventSource | null = new EventSource(url);
  let lastSeq = -1;
  let sawTerminal = false;
  let closed = false;

  const setState = (s: ConnectionState) => handlers.onState?.(s);
  setState("connecting");

  const close = () => {
    if (closed) return;
    closed = true;
    source?.close();
    source = null;
    setState("closed");
  };

  source.onopen = () => {
    if (!closed) setState("open");
  };

  source.onmessage = (msg: MessageEvent<string>) => {
    if (closed) return;
    let event: RunEvent;
    try {
      event = JSON.parse(msg.data) as RunEvent;
    } catch {
      return; // malformed frame; ignore (gap check catches real loss)
    }

    // Drop duplicates / already-seen (reconnect replays the head).
    if (event.seq <= lastSeq) return;

    // A gap: we expected the next seq but got a higher one. Ask for a refetch;
    // still deliver this event so the stream keeps flowing, and let the store's
    // reconcile fill the hole.
    if (lastSeq >= 0 && event.seq > lastSeq + 1) {
      handlers.onGap?.(lastSeq + 1, event.seq);
    }
    lastSeq = event.seq;

    handlers.onEvent(event);

    if (TERMINAL_CHANNELS.has(event.channel)) {
      sawTerminal = true;
    }
    // The run's own close-out is decision -> cost. Once we've seen a terminal
    // channel and then a cost, the run is done; close cleanly.
    if (sawTerminal && event.channel === "cost") {
      handlers.onComplete?.();
      close();
    }
  };

  source.onerror = () => {
    if (closed) return;
    // A clean close after the run finished is expected — not an error.
    if (sawTerminal) {
      handlers.onComplete?.();
      close();
      return;
    }
    // Otherwise it is a real connection issue. EventSource will auto-reconnect
    // (and the backend replays the head, which our seq check dedupes), so we
    // surface the state but do not tear down.
    setState("error");
  };

  return { close };
}
