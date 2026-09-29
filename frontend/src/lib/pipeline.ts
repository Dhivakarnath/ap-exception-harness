import type { RunStatus, StageEntry } from "@/store/runState";

export type PipelineStepId = "extract" | "code_gl" | "policy" | "decide" | "hitl";

export type PipelineStepState = "pending" | "active" | "complete" | "skipped" | "failed";

export interface PipelineStepMeta {
  id: PipelineStepId;
  label: string;
  description: string;
  optional?: boolean;
}

export const PIPELINE_STEPS: PipelineStepMeta[] = [
  {
    id: "extract",
    label: "Extract",
    description: "Parse document and extract invoice fields",
  },
  {
    id: "code_gl",
    label: "GL coding",
    description: "Ground non-PO spend in policy",
    optional: true,
  },
  {
    id: "policy",
    label: "Policy engine",
    description: "Run deterministic checks",
  },
  {
    id: "decide",
    label: "Route",
    description: "Auto-approve, hold, or escalate",
  },
  {
    id: "hitl",
    label: "Escalation",
    description: "Human approver when required",
    optional: true,
  },
];

export interface PipelineHints {
  checkCount?: number;
  hasDecision?: boolean;
}

function hasGraphStages(collapsed: CollapsedStage[]): boolean {
  return collapsed.some((stage) => stage.name !== "run");
}

export interface CollapsedStage {
  name: string;
  done: boolean;
  active: boolean;
  durationMs: number | null;
}

const TERMINAL: RunStatus[] = ["completed", "failed", "awaiting_review"];

/** Pair running/ok stage events into one row per graph node. */
export function collapseStages(
  stages: StageEntry[],
  terminal: boolean,
): CollapsedStage[] {
  const order: string[] = [];
  const byName = new Map<string, CollapsedStage>();
  for (const s of stages) {
    let row = byName.get(s.name);
    if (!row) {
      row = { name: s.name, done: false, active: false, durationMs: null };
      byName.set(s.name, row);
      order.push(s.name);
    }
    if (s.status === "running") {
      row.active = true;
    } else {
      row.done = true;
      row.active = false;
      row.durationMs = s.duration_ms ?? null;
    }
  }
  if (terminal) {
    for (const row of byName.values()) {
      row.done = true;
      row.active = false;
    }
  }
  return order.map((n) => byName.get(n)!);
}

export interface PipelineStepView extends PipelineStepMeta {
  state: PipelineStepState;
  durationMs: number | null;
}

export function buildPipelineSteps(
  stages: StageEntry[],
  status: RunStatus,
  hints: PipelineHints = {},
): PipelineStepView[] {
  const terminal = TERMINAL.includes(status);
  const collapsed = collapseStages(stages, terminal);
  const byName = new Map(collapsed.map((s) => [s.name, s]));
  const runEnvelope = collapsed.find((stage) => stage.name === "run");

  if (
    terminal &&
    !hasGraphStages(collapsed) &&
    ((hints.checkCount ?? 0) > 0 || hints.hasDecision)
  ) {
    return PIPELINE_STEPS.map((meta) => {
      if (meta.id === "hitl" && status === "awaiting_review") {
        return { ...meta, state: "active" as const, durationMs: null };
      }
      if (meta.optional) {
        return { ...meta, state: "skipped" as const, durationMs: null };
      }
      const durationMs =
        meta.id === "extract" && runEnvelope?.durationMs != null
          ? runEnvelope.durationMs
          : null;
      return { ...meta, state: "complete" as const, durationMs };
    });
  }

  if (stages.length === 0 && status === "running") {
    return PIPELINE_STEPS.map((meta, index) => ({
      ...meta,
      state: index === 0 ? ("active" as const) : ("pending" as const),
      durationMs: null,
    }));
  }

  let activeAssigned = false;

  return PIPELINE_STEPS.map((meta) => {
    const row = byName.get(meta.id);

    if (row?.done) {
      return { ...meta, state: "complete" as const, durationMs: row.durationMs };
    }

    if (row?.active && !terminal) {
      activeAssigned = true;
      return { ...meta, state: "active" as const, durationMs: null };
    }

    if (meta.id === "hitl" && status === "awaiting_review") {
      return { ...meta, state: "active" as const, durationMs: null };
    }

    if (terminal) {
      if (meta.optional && !byName.has(meta.id)) {
        return { ...meta, state: "skipped" as const, durationMs: null };
      }
      if (status === "failed" && !row?.done) {
        return { ...meta, state: "failed" as const, durationMs: null };
      }
      if (byName.has(meta.id)) {
        return { ...meta, state: "complete" as const, durationMs: row?.durationMs ?? null };
      }
      return { ...meta, state: "skipped" as const, durationMs: null };
    }

    if (!activeAssigned) {
      activeAssigned = true;
      return { ...meta, state: "active" as const, durationMs: null };
    }

    return { ...meta, state: "pending" as const, durationMs: null };
  });
}

export function formatStageDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  return `${minutes}m ${rem.toFixed(0)}s`;
}
