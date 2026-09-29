import { cn } from "@/lib/cn";
import { EmptyState, Panel } from "@/components/ui/Panel";
import type { RunStatus, StageEntry } from "@/store/runState";

const STAGE_LABEL: Record<string, string> = {
  run: "Run",
  extract: "Extract",
  code_gl: "GL coding",
  policy: "Policy engine",
  decide: "Decide",
  hitl: "Human review",
};

const TERMINAL: RunStatus[] = ["completed", "failed", "awaiting_review"];

/**
 * The pipeline inspector — the run advancing node by node (design §12.1).
 *
 * Folds the paired stage events (a node emits `running` on entry and `ok` on
 * exit) into one row per node with its elapsed time, so the timeline shows the
 * real shape of the run: extract -> (code_gl) -> policy -> decide.
 *
 * The run's overall `status` disambiguates the last node: once the run is
 * terminal, a node still lacking its `ok` event (a sub-second run whose exit
 * event raced the client's connect) is shown as done rather than perpetually
 * "running…". A genuinely in-flight run still pulses its active node.
 */
export function RunInspector({
  stages,
  status = "running",
}: {
  stages: StageEntry[];
  status?: RunStatus;
}) {
  const terminal = TERMINAL.includes(status);
  const rows = collapseStages(stages, terminal);
  return (
    <Panel title="Pipeline" subtitle="Graph nodes, in execution order">
      {rows.length === 0 ? (
        <EmptyState title="No stages yet" />
      ) : (
        <ol className="flex flex-col gap-0 p-2">
          {rows.map((row, i) => {
            const active = !row.done && !terminal;
            return (
              <li key={row.name} className="animate-row-in flex items-stretch gap-3">
                <div className="flex flex-col items-center">
                  <span
                    className={cn(
                      "mt-2.5 size-2.5 rounded-full ring-2 ring-surface",
                      row.done ? "bg-brand" : "bg-skip",
                      active && "bg-brand animate-pulse-soft",
                    )}
                    aria-hidden
                  />
                  {i < rows.length - 1 && (
                    <span className="w-px flex-1 bg-border" aria-hidden />
                  )}
                </div>
                <div className="flex flex-1 items-center justify-between gap-2 py-1.5">
                  <span className="text-sm font-medium text-ink">
                    {STAGE_LABEL[row.name] ?? row.name}
                  </span>
                  <span className="font-mono text-xs text-ink-subtle">
                    {row.durationMs != null
                      ? `${row.durationMs.toFixed(0)} ms`
                      : active
                        ? "running…"
                        : row.done
                          ? "done"
                          : "—"}
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}

interface StageRow {
  name: string;
  done: boolean;
  durationMs: number | null;
}

/**
 * Pair `running`/`ok` stage events into one row per node, preserving order.
 * When the run is `terminal`, every node is treated as done — a node that never
 * received its explicit `ok` (connect race on a sub-second run) is complete by
 * the time the run itself finished.
 */
function collapseStages(stages: StageEntry[], terminal: boolean): StageRow[] {
  const order: string[] = [];
  const byName = new Map<string, StageRow>();
  for (const s of stages) {
    let row = byName.get(s.name);
    if (!row) {
      row = { name: s.name, done: false, durationMs: null };
      byName.set(s.name, row);
      order.push(s.name);
    }
    if (s.status !== "running") {
      row.done = true;
      row.durationMs = s.duration_ms ?? null;
    }
  }
  if (terminal) {
    for (const row of byName.values()) row.done = true;
  }
  return order.map((n) => byName.get(n)!);
}
