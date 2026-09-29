/**
 * RunState — the folded view of a run's event stream (design §12.2).
 *
 * A single pure reducer folds the normalized `RunEvent` stream into this shape.
 * The same reducer serves a live run and a replayed one, so a completed run
 * renders identically to one watched live — the whole point of the single
 * envelope. Components read derived `RunState`; they never touch raw events.
 */

import type {
  CheckPayload,
  CostPayload,
  DecisionPayload,
  ErrorPayload,
  ExtractionField,
  ExtractionLineItem,
  ExtractionMeta,
  HitlPayload,
  RunEvent,
  StagePayload,
} from "@/types/events";

export type RunStatus =
  | "idle"
  | "running"
  | "completed"
  | "failed"
  | "awaiting_review";

export interface StageEntry extends StagePayload {
  seq: number;
  at: string | null;
}

export interface CheckEntry extends CheckPayload {
  seq: number;
  at: string | null;
}

export interface ErrorEntry extends ErrorPayload {
  seq: number;
  at: string | null;
}

export interface RunState {
  runId: string | null;
  traceId: string | null;
  status: RunStatus;
  /** Highest seq folded so far; -1 before any event. */
  lastSeq: number;

  stages: StageEntry[];
  checks: CheckEntry[];
  extraction: ExtractionField[];
  /** The extracted line table. Empty until a line-carrying extraction arrives. */
  lineItems: ExtractionLineItem[];
  /** How the fields were extracted (Docling parse + Bedrock). Null until an
   *  extraction event with process metadata arrives. */
  extractionMeta: ExtractionMeta | null;
  decision: DecisionPayload | null;
  hitl: HitlPayload | null;
  cost: CostPayload | null;
  errors: ErrorEntry[];
}

export function emptyRunState(runId: string | null = null): RunState {
  return {
    runId,
    traceId: null,
    status: runId ? "running" : "idle",
    lastSeq: -1,
    stages: [],
    checks: [],
    extraction: [],
    lineItems: [],
    extractionMeta: null,
    decision: null,
    hitl: null,
    cost: null,
    errors: [],
  };
}

/**
 * Fold one event into the state, returning a new state (pure, immutable).
 *
 * Idempotent on seq: an event at or below `lastSeq` is ignored, so a reconnect
 * replay of the stream head cannot double-append. The reducer never mutates the
 * input.
 */
export function reduceEvent(state: RunState, event: RunEvent): RunState {
  if (event.seq <= state.lastSeq) {
    return state;
  }

  const base: RunState = {
    ...state,
    runId: state.runId ?? event.run_id,
    traceId: state.traceId ?? event.trace_id,
    lastSeq: event.seq,
  };

  switch (event.channel) {
    case "stage":
      return {
        ...base,
        stages: [...base.stages, { ...event.payload, seq: event.seq, at: event.at }],
      };

    case "check":
      return {
        ...base,
        checks: [...base.checks, { ...event.payload, seq: event.seq, at: event.at }],
      };

    case "extraction":
      return {
        ...base,
        // A later extraction snapshot supersedes an earlier partial one.
        extraction: event.payload.fields ?? base.extraction,
        lineItems: event.payload.line_items ?? base.lineItems,
        extractionMeta: event.payload.meta ?? base.extractionMeta,
      };

    case "decision": {
      const { route, actor } = event.payload;
      // `route_for_approval` means "awaiting a human" only while the policy
      // engine/agent proposed it. Once a human has acted (resume), the same
      // route is the resolved, terminal outcome — the run is complete, not
      // still pending. Keying off the actor is what distinguishes the two.
      const pendingReview = route === "route_for_approval" && actor !== "human";
      const status: RunStatus = pendingReview ? "awaiting_review" : "completed";
      return { ...base, decision: event.payload, status };
    }

    case "hitl":
      return { ...base, hitl: event.payload, status: "awaiting_review" };

    case "cost":
      return { ...base, cost: event.payload };

    case "error":
      return {
        ...base,
        status: "failed",
        errors: [...base.errors, { ...event.payload, seq: event.seq, at: event.at }],
      };

    case "tool":
      // Tool events are shown in the trace/timeline; they do not change the
      // folded run summary here. Attaching them to stages keeps the shape lean
      // while remaining faithful (a tool call is a sub-step of a stage).
      return base;

    default:
      return base;
  }
}

/** Fold a whole event list from scratch (used for replay / snapshot rebuild). */
export function reduceAll(events: RunEvent[], runId: string | null = null): RunState {
  return events.reduce(reduceEvent, emptyRunState(runId));
}

/** Convenience selectors the views use. */
export const selectors = {
  verdictCounts(state: RunState): Record<string, number> {
    const counts: Record<string, number> = { pass: 0, flag: 0, fail: 0, skip: 0 };
    for (const c of state.checks) {
      counts[c.verdict] = (counts[c.verdict] ?? 0) + 1;
    }
    return counts;
  },
  isTerminal(state: RunState): boolean {
    return (
      state.status === "completed" ||
      state.status === "failed" ||
      state.status === "awaiting_review"
    );
  },
};
