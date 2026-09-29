import { describe, expect, it } from "vitest";
import {
  emptyRunState,
  reduceAll,
  reduceEvent,
  selectors,
} from "@/store/runState";
import type { RunEvent, Verdict } from "@/types/events";

function stage(seq: number, name: string, status = "ok"): RunEvent {
  return {
    run_id: "r",
    trace_id: "t",
    seq,
    channel: "stage",
    at: "2026-02-15T00:00:00Z",
    payload: { name, status },
  };
}

function check(seq: number, name: string, verdict: Verdict): RunEvent {
  return {
    run_id: "r",
    trace_id: "t",
    seq,
    channel: "check",
    at: null,
    payload: { name, category: "integrity", verdict, severity: "info", reasoning: "" },
  };
}

function decision(
  seq: number,
  route: string,
  actor: "agent" | "human" | "policy_engine" = "agent",
): RunEvent {
  return {
    run_id: "r",
    trace_id: "t",
    seq,
    channel: "decision",
    at: null,
    payload: { route: route as never, rationale: "because", actor },
  };
}

function cost(seq: number): RunEvent {
  return {
    run_id: "r",
    trace_id: "t",
    seq,
    channel: "cost",
    at: null,
    payload: { input_tokens: 1200, output_tokens: 180, usd: 0.000115 },
  };
}

function errorEvent(seq: number): RunEvent {
  return {
    run_id: "r",
    trace_id: "t",
    seq,
    channel: "error",
    at: null,
    payload: { error_type: "ExtractionError", message: "boom", stage: "extract" },
  };
}

describe("reduceEvent", () => {
  it("captures run + trace id from the first event", () => {
    const s = reduceEvent(emptyRunState(), stage(0, "run", "running"));
    expect(s.runId).toBe("r");
    expect(s.traceId).toBe("t");
    expect(s.lastSeq).toBe(0);
  });

  it("appends checks in evaluation order", () => {
    const events = [
      check(0, "completeness", "pass"),
      check(1, "math_integrity", "pass"),
      check(2, "three_way_match", "skip"),
    ];
    const s = reduceAll(events);
    expect(s.checks.map((c) => c.name)).toEqual([
      "completeness",
      "math_integrity",
      "three_way_match",
    ]);
  });

  it("is idempotent on seq (drops duplicates / replayed head)", () => {
    let s = emptyRunState();
    s = reduceEvent(s, check(0, "completeness", "pass"));
    s = reduceEvent(s, check(1, "math_integrity", "pass"));
    // Replay of already-seen events (e.g. after a reconnect).
    s = reduceEvent(s, check(0, "completeness", "pass"));
    s = reduceEvent(s, check(1, "math_integrity", "pass"));
    expect(s.checks).toHaveLength(2);
  });

  it("maps auto_approve decision to completed", () => {
    const s = reduceEvent(emptyRunState("r"), decision(0, "auto_approve"));
    expect(s.status).toBe("completed");
    expect(s.decision?.route).toBe("auto_approve");
  });

  it("maps an agent's route_for_approval decision to awaiting_review", () => {
    const s = reduceEvent(emptyRunState("r"), decision(0, "route_for_approval"));
    expect(s.status).toBe("awaiting_review");
  });

  it("maps a human-resolved route_for_approval to completed (post-resume)", () => {
    // After a HITL resume the persisted decision keeps route_for_approval but
    // is now authored by the human — the run is resolved, not still pending.
    const s = reduceEvent(
      emptyRunState("r"),
      decision(0, "route_for_approval", "human"),
    );
    expect(s.status).toBe("completed");
  });

  it("records cost", () => {
    const s = reduceEvent(emptyRunState("r"), cost(0));
    expect(s.cost).toEqual({ input_tokens: 1200, output_tokens: 180, usd: 0.000115 });
  });

  it("marks the run failed and records the error", () => {
    const s = reduceEvent(emptyRunState("r"), errorEvent(0));
    expect(s.status).toBe("failed");
    expect(s.errors[0]?.message).toBe("boom");
    expect(s.errors[0]?.stage).toBe("extract");
  });

  it("does not mutate the input state", () => {
    const before = emptyRunState("r");
    const frozen = Object.freeze({ ...before, checks: Object.freeze([]) });
    // reduceEvent must return a new object, never mutate — this throws if it does.
    expect(() => reduceEvent(frozen as never, check(0, "x", "pass"))).not.toThrow();
  });
});

describe("reduceAll — replay equals live", () => {
  it("produces the same state whether folded all-at-once or one-by-one", () => {
    const events = [
      stage(0, "run", "running"),
      check(1, "completeness", "pass"),
      check(2, "math_integrity", "flag"),
      decision(3, "auto_approve"),
      cost(4),
    ];
    const batch = reduceAll(events, "r");
    const live = events.reduce(reduceEvent, emptyRunState("r"));
    expect(batch).toEqual(live);
    expect(batch.status).toBe("completed");
    expect(batch.checks).toHaveLength(2);
    expect(batch.cost?.usd).toBe(0.000115);
  });
});

describe("selectors", () => {
  it("counts verdicts", () => {
    const s = reduceAll([
      check(0, "a", "pass"),
      check(1, "b", "pass"),
      check(2, "c", "flag"),
      check(3, "d", "skip"),
    ]);
    expect(selectors.verdictCounts(s)).toEqual({ pass: 2, flag: 1, fail: 0, skip: 1 });
  });

  it("reports terminal status", () => {
    expect(selectors.isTerminal(reduceAll([decision(0, "auto_approve")]))).toBe(true);
    expect(selectors.isTerminal(reduceAll([check(0, "a", "pass")]))).toBe(false);
  });
});
