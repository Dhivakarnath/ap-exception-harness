import { cn } from "@/lib/cn";
import {
  buildPipelineSteps,
  formatStageDuration,
  type PipelineStepState,
  type PipelineStepView,
} from "@/lib/pipeline";
import { EmptyState, Panel } from "@/components/ui/Panel";
import type { RunStatus, StageEntry } from "@/store/runState";

const STATE_STYLES: Record<
  PipelineStepState,
  { ring: string; fill: string; connector: string; label: string }
> = {
  pending: {
    ring: "border-border bg-surface-2 text-ink-subtle",
    fill: "bg-skip",
    connector: "bg-border",
    label: "text-ink-subtle",
  },
  active: {
    ring: "border-brand bg-brand-soft text-brand-ink shadow-[0_0_0_3px_var(--color-brand-soft)]",
    fill: "bg-brand animate-pulse-soft",
    connector: "bg-brand/40",
    label: "text-ink",
  },
  complete: {
    ring: "border-brand bg-brand text-white",
    fill: "bg-brand",
    connector: "bg-brand",
    label: "text-ink",
  },
  skipped: {
    ring: "border-border/80 bg-surface-2 text-ink-subtle border-dashed",
    fill: "bg-skip",
    connector: "bg-border/60",
    label: "text-ink-subtle",
  },
  failed: {
    ring: "border-fail bg-fail-soft text-fail-ink",
    fill: "bg-fail",
    connector: "bg-fail/50",
    label: "text-fail-ink",
  },
};

/**
 * Horizontal pipeline — graph nodes left-to-right in execution order.
 * Designed for observability: status, timing, and branch-specific skips at a glance.
 */
export function PipelineFlow({
  stages,
  status = "running",
  checkCount = 0,
  hasDecision = false,
}: {
  stages: StageEntry[];
  status?: RunStatus;
  checkCount?: number;
  hasDecision?: boolean;
}) {
  const steps = buildPipelineSteps(stages, status, { checkCount, hasDecision });
  const hasActivity = stages.length > 0 || status !== "idle";

  return (
    <Panel
      title="Processing pipeline"
      subtitle="Supervisor graph · left to right in execution order"
      bodyClassName="px-4 py-5 sm:px-6"
    >
      {!hasActivity ? (
        <EmptyState
          title="Waiting for pipeline"
          hint="Stages appear here as soon as the run connects to the live stream."
        />
      ) : (
        <div
          className="overflow-x-auto pb-1"
          role="list"
          aria-label="Pipeline stages in execution order"
        >
          <ol className="flex w-full min-w-[40rem] items-start">
            {steps.map((step, index) => (
              <PipelineStep
                key={step.id}
                step={step}
                isLast={index === steps.length - 1}
              />
            ))}
          </ol>
        </div>
      )}
    </Panel>
  );
}

function PipelineStep({
  step,
  isLast,
}: {
  step: PipelineStepView;
  isLast: boolean;
}) {
  const styles = STATE_STYLES[step.state];

  return (
    <li className="flex min-w-0 flex-1 items-start last:flex-none last:min-w-[7.5rem]" role="listitem">
      <div className="flex w-[7.5rem] shrink-0 flex-col items-center gap-2 px-1 sm:w-[8.5rem]">
        <div
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold tabular-nums transition-colors",
            styles.ring,
          )}
          aria-hidden
        >
          {step.state === "complete" ? (
            <CheckIcon />
          ) : step.state === "skipped" ? (
            <span className="text-[10px] font-medium uppercase tracking-wide">N/A</span>
          ) : (
            <span>{stepIndexLabel(step.id)}</span>
          )}
        </div>
        <div className="w-full text-center">
          <p className={cn("text-sm font-medium leading-tight", styles.label)}>
            {step.label}
          </p>
          <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-ink-subtle">
            {step.description}
          </p>
          <p className="mt-1 font-mono text-[11px] text-ink-subtle">
            {step.state === "active"
              ? "Running…"
              : step.state === "skipped"
                ? "Not on this path"
                : formatStageDuration(step.durationMs)}
          </p>
        </div>
      </div>
      {!isLast && (
        <div className="mt-[1.125rem] h-0.5 min-w-[0.75rem] flex-1 px-0.5" aria-hidden>
          <div className={cn("h-full w-full rounded-full", styles.connector)} />
        </div>
      )}
    </li>
  );
}

function stepIndexLabel(id: PipelineStepView["id"]): string {
  const order: Record<PipelineStepView["id"], string> = {
    extract: "1",
    code_gl: "2",
    policy: "3",
    decide: "4",
    hitl: "5",
  };
  return order[id];
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" className="size-4" fill="none" aria-hidden>
      <path
        d="M3.5 8.5 6.5 11.5 12.5 4.5"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
