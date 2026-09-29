import { describe, expect, it } from "vitest";
import { buildPipelineSteps, collapseStages } from "@/lib/pipeline";
import type { StageEntry } from "@/store/runState";

function stage(name: string, status: StageEntry["status"], duration_ms?: number): StageEntry {
  return {
    name,
    status,
    duration_ms: duration_ms ?? null,
    seq: 0,
    at: null,
  };
}

describe("collapseStages", () => {
  it("pairs running and ok events for the same node", () => {
    const rows = collapseStages(
      [
        stage("extract", "running"),
        stage("extract", "ok", 1200),
        stage("policy", "running"),
      ],
      false,
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ name: "extract", done: true, durationMs: 1200 });
    expect(rows[1]).toMatchObject({ name: "policy", active: true, done: false });
  });
});

describe("buildPipelineSteps", () => {
  it("marks extract active when the run is live with no events yet", () => {
    const steps = buildPipelineSteps([], "running");
    expect(steps[0]?.state).toBe("active");
    expect(steps[1]?.state).toBe("pending");
  });

  it("skips optional branches on a completed PO path", () => {
    const steps = buildPipelineSteps(
      [
        stage("extract", "ok", 900),
        stage("policy", "ok", 200),
        stage("decide", "ok", 50),
      ],
      "completed",
    );
    expect(steps.find((s) => s.id === "code_gl")?.state).toBe("skipped");
    expect(steps.find((s) => s.id === "hitl")?.state).toBe("skipped");
    expect(steps.find((s) => s.id === "policy")?.state).toBe("complete");
  });

  it("infers completed graph stages from replay envelope plus checks", () => {
    const steps = buildPipelineSteps(
      [stage("run", "ok", 4044)],
      "completed",
      { checkCount: 14, hasDecision: true },
    );
    expect(steps.find((s) => s.id === "extract")?.state).toBe("complete");
    expect(steps.find((s) => s.id === "policy")?.state).toBe("complete");
    expect(steps.find((s) => s.id === "decide")?.state).toBe("complete");
    expect(steps.find((s) => s.id === "code_gl")?.state).toBe("skipped");
    expect(steps.find((s) => s.id === "hitl")?.state).toBe("skipped");
  });
});
