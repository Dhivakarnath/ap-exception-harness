import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { PageState } from "@/components/layout/PageState";
import { Badge, RouteBadge } from "@/components/ui/Badge";
import { ChevronIcon } from "@/components/ui/icons";
import { EmptyState, Panel } from "@/components/ui/Panel";
import { cn } from "@/lib/cn";
import { ALL_TENANTS, tenantShortLabel, type TenantFilter } from "@/lib/tenants";
import { useTenantFilter } from "@/store/useTenantFilter";
import { fetchEvals } from "@/transport/api";
import type { AsyncState } from "@/lib/useAsync";
import type {
  EvaluationStatus,
  EvalsReport,
  LiveEvaluation,
  ManifestScorecard,
} from "@/types/events";

const REFRESH_MS = 3_000;
const WEAK_THRESHOLD = 0.8;
/** Manifest gate: green only at ≥90%; 80–89% passes CI but shows amber. */
const STRONG_THRESHOLD = 0.9;
const CI_GATE_THRESHOLD = 0.8;

export function EvalsPage() {
  const { tenant } = useTenantFilter();
  const state = useLiveEvaluations(tenant);

  return (
    <PageBody className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Live quality"
        title="Run evaluations"
        description="Two layers: the manifest CI gate (synthetic dataset, deterministic) and per-upload Bedrock judges (real invoices). Percentages are quality scores; summary tiles are run counts."
      />
      <PageState state={state}>
        {(report) => (
          <EvalsContent report={report} showTenant={tenant === ALL_TENANTS} />
        )}
      </PageState>
    </PageBody>
  );
}

function useLiveEvaluations(tenant: TenantFilter): AsyncState<EvalsReport> {
  const [state, setState] = useState<AsyncState<EvalsReport>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const refresh = async () => {
      try {
        const data = await fetchEvals(tenant);
        if (!cancelled) {
          setState({ data, error: null, loading: false });
          timer = window.setTimeout(refresh, REFRESH_MS);
        }
      } catch (error) {
        if (!cancelled) {
          setState((current) => ({
            data: current.data,
            error: error instanceof Error ? error.message : "Failed to load evaluations",
            loading: false,
          }));
          timer = window.setTimeout(refresh, REFRESH_MS);
        }
      }
    };

    void refresh();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [tenant]);

  return state;
}

export function EvalsContent({
  report,
  showTenant = false,
}: {
  report: EvalsReport;
  showTenant?: boolean;
}) {
  const scorecard = report.manifest_scorecard;

  if (report.evaluations.length === 0) {
    return (
      <>
        {scorecard ? <ManifestScorecardPanel scorecard={scorecard} /> : null}
        <Panel title="Evaluated runs">
          <EmptyState
            title="No evaluated documents yet"
            hint={
              report.message ??
              "Upload an invoice from Runs. Scripted demos are excluded; only live Bedrock documents are scored."
            }
          />
        </Panel>
      </>
    );
  }

  return (
    <>
      {scorecard ? <ManifestScorecardPanel scorecard={scorecard} /> : null}
      <EvalSummary evaluations={report.evaluations} />
      <EvaluationList evaluations={report.evaluations} showTenant={showTenant} />
    </>
  );
}

function ManifestScorecardPanel({ scorecard }: { scorecard: ManifestScorecard }) {
  if (scorecard.status !== "available") {
    return (
      <Panel
        title="Manifest eval gate"
        subtitle="Synthetic adversarial dataset — run `make eval` locally"
        bodyClassName="p-4 sm:p-5"
      >
        <p className="text-sm text-ink-subtle">
          {scorecard.message ?? "No CI scorecard on disk yet."}
        </p>
      </Panel>
    );
  }

  const live = scorecard.live;
  const hasLive = live != null && Object.values(live).some((v) => v != null);

  const liveTiles = hasLive && live
    ? [
        { label: "Live E2E", value: live.task_completion, hint: "DeepEval on real supervisor runs." },
        { label: "Tool correctness", value: live.tool_correctness, hint: "Required ERP tools called (manifest live sample)." },
        {
          label: "Argument correctness",
          value: live.argument_correctness,
          hint: "ERP call arguments matched invoice context (manifest live sample).",
        },
        { label: "RAG faithfulness", value: live.rag_faithfulness, hint: "GL answer inside retrieved policy." },
        { label: "RAG answer", value: live.rag_answer_relevancy, hint: "Answer addressed the coding question." },
        { label: "RAG context", value: live.rag_contextual_relevancy, hint: "Retrieved chunks were useful." },
        { label: "Extraction", value: live.extraction_accuracy, hint: "Header fields vs manifest ground truth." },
      ]
    : [];

  return (
    <Panel
      title="Manifest eval gate"
      subtitle="Synthetic dataset · from make eval · not uploaded invoices"
      bodyClassName="flex flex-col gap-5 p-4 sm:p-5"
    >
      <section className="w-full space-y-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
          Deterministic CI gate (percentages)
        </h3>
        <div
          className="grid w-full grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2"
        >
          <ManifestMetricTile
            label="Policy adherence"
            value={scorecard.policy_adherence}
            hint="Real policy engine on the adversarial manifest. Primary CI gate (≥80%)."
            emphasize
          />
          <ManifestMetricTile
            label="Routing label consistency"
            value={scorecard.routing_label_consistency}
            hint="Route prediction vs labels — no expected_route / expected_gl peek."
            emphasize
          />
        </div>
      </section>

      {liveTiles.length > 0 ? (
        <section className="w-full space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
            Live Bedrock sample (percentages · small run count)
          </h3>
          <div
            className="grid w-full grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7"
          >
            {liveTiles.map((tile) => (
              <ManifestMetricTile
                key={tile.label}
                label={tile.label}
                value={tile.value}
                hint={tile.hint}
              />
            ))}
          </div>
        </section>
      ) : null}
    </Panel>
  );
}

function ManifestMetricTile({
  label,
  value,
  hint,
  emphasize = false,
}: {
  label: string;
  value: number | null | undefined;
  hint: string;
  emphasize?: boolean;
}) {
  return (
    <div
      title={hint}
      className={cn(
        "flex min-h-[4.5rem] flex-col justify-center bg-surface px-4 py-3",
        emphasize && "sm:min-h-[5.5rem]",
      )}
    >
      <p className="text-xs leading-snug text-ink-subtle">{label}</p>
      <p
        className={cn(
          "mt-1 font-mono font-semibold tabular-nums",
          emphasize ? "text-2xl" : "text-lg",
          manifestScoreClass(value ?? null),
        )}
      >
        {formatPercent(value ?? null)}
      </p>
    </div>
  );
}

function manifestScoreClass(value: number | null): string {
  if (value == null) return "text-ink-muted";
  if (value >= STRONG_THRESHOLD) return "text-pass-ink";
  if (value >= CI_GATE_THRESHOLD) return "text-flag-ink";
  if (value > 0) return "text-fail-ink";
  return "text-fail-ink";
}

function EvalSummary({ evaluations }: { evaluations: LiveEvaluation[] }) {
  const completed = evaluations.filter((item) => item.status === "completed");
  const scoring = evaluations.filter(
    (item) => item.status === "pending" || item.status === "running",
  ).length;
  const weak = completed.filter(hasWeakCategory).length;
  const ragUnused = completed.filter((item) => !item.rag_applicable).length;
  const withGroundTruth = completed.filter((item) => item.extraction_applicable).length;
  const afterHitl = completed.filter((item) => item.evaluated_after_hitl).length;

  const tiles = [
    {
      label: "Evaluated",
      value: completed.length,
      unit: completed.length === 1 ? "run" : "runs",
      description: "Uploads with a finished Bedrock scorecard.",
    },
    {
      label: "In progress",
      value: scoring,
      unit: scoring === 1 ? "run" : "runs",
      description: "Queued or judging now — a count, not a quality %.",
    },
    {
      label: "Below 80%",
      value: weak,
      unit: weak === 1 ? "run" : "runs",
      description: "Weakest applicable category under 80%.",
    },
    {
      label: "Manifest match",
      value: withGroundTruth,
      unit: withGroundTruth === 1 ? "run" : "runs",
      description: "Linked to dataset extraction ground truth.",
    },
    {
      label: "RAG skipped",
      value: ragUnused,
      unit: ragUnused === 1 ? "run" : "runs",
      description: "PO-backed — GL from PO, not RAG-scored.",
    },
    {
      label: "Post-HITL",
      value: afterHitl,
      unit: afterHitl === 1 ? "run" : "runs",
      description: "Re-scored after human review.",
    },
  ];

  return (
    <Panel
      title="Uploaded documents"
      subtitle="Summary counts — not percentages"
      bodyClassName="p-4 sm:p-5"
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {tiles.map((tile) => (
          <SummaryCountTile key={tile.label} {...tile} />
        ))}
      </div>
    </Panel>
  );
}

function SummaryCountTile({
  label,
  value,
  unit,
  description,
}: {
  label: string;
  value: number;
  unit: string;
  description: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface-2/30 px-3 py-3">
      <p className="text-xs font-medium text-ink-subtle">{label}</p>
      <p className="mt-1.5 font-mono text-2xl font-semibold tabular-nums text-ink">
        {value}
        <span className="ml-1.5 text-sm font-normal text-ink-muted">{unit}</span>
      </p>
      <p className="mt-1.5 text-[11px] leading-snug text-ink-subtle">{description}</p>
    </div>
  );
}

function EvaluationList({
  evaluations,
  showTenant,
}: {
  evaluations: LiveEvaluation[];
  showTenant: boolean;
}) {
  const [openIds, setOpenIds] = useState<string[]>([]);

  useEffect(() => {
    const live = new Set(evaluations.map((item) => item.run_id));
    setOpenIds((current) => {
      const next = current.filter((id) => live.has(id));
      return next.length === current.length ? current : next;
    });
  }, [evaluations]);

  return (
    <Panel
      title="Evaluated runs"
      subtitle={`${evaluations.length} upload(s) · row scores are percentages · newest first`}
    >
      <ul className="divide-y divide-border">
        {evaluations.map((evaluation) => (
          <EvaluationRow
            key={evaluation.run_id}
            evaluation={evaluation}
            showTenant={showTenant}
            open={openIds.includes(evaluation.run_id)}
            onToggle={() =>
              setOpenIds((current) =>
                current.includes(evaluation.run_id)
                  ? current.filter((id) => id !== evaluation.run_id)
                  : [...current, evaluation.run_id],
              )
            }
          />
        ))}
      </ul>
    </Panel>
  );
}

function EvaluationRow({
  evaluation,
  showTenant,
  open,
  onToggle,
}: {
  evaluation: LiveEvaluation;
  showTenant: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const panelId = useId();
  const pending = evaluation.status !== "completed";
  const ragNA = !evaluation.rag_applicable && evaluation.status === "completed";
  const extractionNA =
    !evaluation.extraction_applicable && evaluation.status === "completed";
  const evidence = toolEvidence(evaluation);

  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className={cn(
          "flex w-full items-start gap-3 px-4 py-3.5 text-left transition-colors",
          "hover:bg-surface-2 focus-visible:bg-surface-2",
          open && "bg-surface-2/70",
        )}
      >
        <ChevronIcon
          className={cn(
            "mt-1 size-4 shrink-0 text-ink-subtle transition-transform duration-[var(--dur-base)] ease-[var(--ease-out-quint)]",
            open && "rotate-90",
          )}
        />
        <div className="flex min-w-0 flex-1 flex-col gap-3 lg:flex-row lg:items-center lg:gap-6">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="truncate text-sm font-medium text-ink">
                {evaluation.invoice_number}
              </span>
              {showTenant ? (
                <Badge tone="neutral">{tenantShortLabel(evaluation.tenant_id)}</Badge>
              ) : null}
              <InvoicePathBadge isNonPo={evaluation.is_non_po} />
              <ExtractionGroundTruthBadge evaluation={evaluation} />
              {evaluation.evaluated_after_hitl ? (
                <Badge tone="flag">Post-HITL</Badge>
              ) : null}
              <EvaluationStatusBadge status={evaluation.status} />
            </div>
            <p className="mt-0.5 truncate text-xs text-ink-subtle">
              {evaluation.vendor}
              {evaluation.evaluated_at ? ` · ${formatTime(evaluation.evaluated_at)}` : ""}
            </p>
          </div>
          <div className="grid w-full grid-cols-3 gap-2 sm:grid-cols-5 sm:w-auto sm:min-w-[24rem]">
            <ScoreTile
              label="Policy"
              value={evaluation.metrics.policy_adherence}
              pending={pending}
              title="Deterministic policy ledger vs manifest expectations (%)."
            />
            <ScoreTile
              label="E2E"
              value={evaluation.metrics.task_completion}
              pending={pending}
              title="Live DeepEval: did the supervisor complete the workflow (%)?"
            />
            <ScoreTile
              label="Tools"
              value={evaluation.metrics.tool_use}
              pending={pending}
              title="Deterministic ERP tool selection and argument match — min of both (%)."
            />
            <ScoreTile
              label="RAG"
              value={evaluation.metrics.rag_grounding}
              pending={pending}
              notApplicable={ragNA}
              title={ragNA ? ragNotApplicableHint(evaluation) : "Lowest RAG triad sub-score (%)."}
            />
            <ScoreTile
              label="Extract"
              value={evaluation.metrics.extraction_accuracy}
              pending={pending}
              notApplicable={extractionNA}
              title={
                extractionNA
                  ? extractionNotApplicableHint(evaluation)
                  : "Header fields vs manifest ground truth when catalog matches (%)."
              }
            />
          </div>
        </div>
      </button>

      {open ? (
        <div
          id={panelId}
          className="border-t border-border bg-surface-2/40 px-4 py-4 sm:px-5"
        >
          {evaluation.status === "failed" ? (
            <div
              role="alert"
              className="mb-4 rounded-md border border-fail/30 bg-fail-soft px-3 py-2.5 text-sm text-fail-ink"
            >
              Evaluation failed: {evaluation.error ?? "Unknown judge error"}
            </div>
          ) : null}

          <div className="divide-y divide-border">
            <CategoryDetail
              title="Policy adherence"
              reading={policyReading(evaluation)}
              value={evaluation.metrics.policy_adherence}
              pending={pending}
              mismatches={evaluation.breakdown?.policy?.mismatches ?? []}
            />
            <CategoryDetail
              title="End-to-end completion"
              reading={taskReading(evaluation)}
              value={evaluation.metrics.task_completion}
              pending={pending}
              steps={apTaskChecklist(evaluation)}
              judgeReason={evaluation.breakdown?.task?.reason ?? null}
            />
            <CategoryDetail
              title="Tool use"
              reading={toolReading(evaluation)}
              value={evaluation.metrics.tool_use}
              pending={pending}
              components={toolComponents(evaluation)}
              detailLabel="Scoring"
              judgeReason={evaluation.breakdown?.tools?.reason ?? null}
              mismatches={evaluation.breakdown?.tools?.mismatches ?? []}
              {...(evidence ? { evidence } : {})}
            />
            <CategoryDetail
              title="RAG grounding"
              reading={ragReading(evaluation)}
              value={evaluation.metrics.rag_grounding}
              pending={pending}
              notApplicable={ragNA}
              components={ragComponents(evaluation)}
            />
            <CategoryDetail
              title="Extraction accuracy"
              reading={extractionReading(evaluation)}
              value={evaluation.metrics.extraction_accuracy}
              pending={pending}
              notApplicable={extractionNA}
              mismatches={evaluation.breakdown?.extraction?.mismatches ?? []}
            />
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 text-xs text-ink-subtle">
            <p>
              {evaluation.evaluated_at
                ? `Judged ${formatTime(evaluation.evaluated_at)} · ${evaluation.judge_model}`
                : `Judge ${evaluation.judge_model} · scoring in progress`}
            </p>
            <div className="flex items-center gap-3">
              {evaluation.route ? <RouteBadge route={evaluation.route} /> : null}
              <Link
                to={`/runs/${evaluation.run_id}`}
                className="font-medium text-brand-ink hover:underline"
                onClick={(event) => event.stopPropagation()}
              >
                Open run
              </Link>
            </div>
          </div>
        </div>
      ) : null}
    </li>
  );
}

function CategoryDetail({
  title,
  reading,
  value,
  pending,
  notApplicable = false,
  components = [],
  evidence,
  mismatches = [],
  steps = [],
  judgeReason = null,
  detailLabel = "Judge",
}: {
  title: string;
  reading: string;
  value: number | null;
  pending: boolean;
  notApplicable?: boolean;
  components?: Array<{ label: string; description: string; value: number | null }>;
  evidence?: { actual: string[]; expected: string[] };
  mismatches?: string[];
  steps?: string[];
  judgeReason?: string | null;
  detailLabel?: string;
}) {
  return (
    <details open className="group py-4 first:pt-0 last:pb-0">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-md px-1 py-1 marker:content-none [&::-webkit-details-marker]:hidden">
        <span className="flex min-w-0 items-center gap-2">
          <ChevronIcon className="size-3.5 shrink-0 text-ink-subtle transition-transform duration-[var(--dur-base)] ease-[var(--ease-out-quint)] group-open:rotate-90" />
          <span className="text-sm font-medium text-ink">{title}</span>
        </span>
        <span
          className={cn(
            "font-mono text-xl font-semibold tabular-nums",
            scoreClass(value, { pending, notApplicable }),
          )}
        >
          {formatPercent(value, { pending, notApplicable })}
        </span>
      </summary>
      <div className="mt-3 pl-6">
        <p className="max-w-prose text-sm leading-relaxed text-ink-muted">{reading}</p>
        {steps.length > 0 ? (
          <div className="mt-4">
            <p className="text-[11px] font-medium text-ink-subtle">Workflow judged</p>
            <ul className="mt-2 space-y-1.5 text-sm text-ink-muted">
              {steps.map((step) => (
                <li key={step} className="flex gap-2">
                  <span className="text-ink-subtle" aria-hidden>·</span>
                  <span>{step}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {judgeReason ? (
          <p className="mt-4 max-w-prose text-sm leading-relaxed text-ink-subtle">
            <span className="font-medium text-ink-muted">{detailLabel}: </span>
            {judgeReason}
          </p>
        ) : null}
        {components.length > 0 ? (
          <ul
            className={cn(
              "mt-4 grid gap-4",
              components.length > 1 ? "sm:grid-cols-2" : "grid-cols-1",
            )}
          >
            {components.map((component) => (
              <li key={component.label}>
                <ComponentMeter
                  label={component.label}
                  description={component.description}
                  value={component.value}
                />
              </li>
            ))}
          </ul>
        ) : null}
        {evidence ? <ToolCallEvidence actual={evidence.actual} expected={evidence.expected} /> : null}
        {mismatches.length > 0 ? <MismatchList items={mismatches} /> : null}
      </div>
    </details>
  );
}

function MismatchList({ items }: { items: string[] }) {
  return (
    <div className="mt-4">
      <p className="text-[11px] font-medium text-ink-subtle">Mismatches</p>
      <ul className="mt-1 space-y-0.5 font-mono text-[11px] leading-relaxed text-ink-muted">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function ComponentMeter({
  label,
  description,
  value,
}: {
  label: string;
  description: string;
  value: number | null;
}) {
  const percentage = value == null ? null : Math.round(value * 100);
  const bar =
    percentage == null ? "bg-skip" : percentage >= 80 ? "bg-pass" : percentage === 0 ? "bg-fail" : "bg-flag";

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-xs font-medium text-ink">{label}</p>
        <p className="font-mono text-xs text-ink-muted">{formatPercent(value)}</p>
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percentage ?? undefined}
      >
        {percentage != null ? (
          <div className={cn("h-full rounded-full", bar)} style={{ width: `${percentage}%` }} />
        ) : null}
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-ink-subtle">{description}</p>
    </div>
  );
}

function ToolCallEvidence({ actual, expected }: { actual: string[]; expected: string[] }) {
  if (actual.length === 0 && expected.length === 0) return null;

  return (
    <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
      <CallList label="Called" names={actual} />
      <CallList label="Expected" names={expected} />
    </div>
  );
}

function CallList({ label, names }: { label: string; names: string[] }) {
  return (
    <div>
      <p className="text-[11px] font-medium text-ink-subtle">{label}</p>
      <ul className="mt-1 space-y-0.5 font-mono text-[11px] leading-relaxed text-ink-muted">
        {(names.length > 0 ? names : ["none"]).map((name) => (
          <li key={`${label}-${name}`}>{name}</li>
        ))}
      </ul>
    </div>
  );
}

function ScoreTile({
  label,
  value,
  pending,
  notApplicable = false,
  title,
}: {
  label: string;
  value: number | null;
  pending: boolean;
  notApplicable?: boolean;
  title?: string;
}) {
  return (
    <div
      title={title}
      className="rounded-md border border-border bg-surface px-2.5 py-2 text-right"
    >
      <p className="text-[11px] leading-none text-ink-subtle">{label}</p>
      <p
        className={cn(
          "mt-1 font-mono text-sm font-semibold tabular-nums",
          scoreClass(value, { pending, notApplicable }),
        )}
      >
        {formatPercent(value, { pending, notApplicable })}
      </p>
    </div>
  );
}

function InvoicePathBadge({ isNonPo }: { isNonPo: boolean | null }) {
  if (isNonPo === null) return null;
  return (
    <Badge tone={isNonPo ? "brand" : "neutral"}>
      {isNonPo ? "Non-PO" : "PO-backed"}
    </Badge>
  );
}

function tenantGroundTruthLabel(tenantId: string): string {
  if (tenantId === "retail-demo") return "Retail ground truth";
  if (tenantId === "manufacturing-demo") return "Manufacturing ground truth";
  return `${tenantId} ground truth`;
}

function ExtractionGroundTruthBadge({ evaluation }: { evaluation: LiveEvaluation }) {
  if (evaluation.status !== "completed") return null;
  const extraction = evaluation.breakdown?.extraction;
  const status = extraction?.ground_truth_status;

  if (status === "matched" && extraction?.ground_truth_tenant) {
    return (
      <Badge tone="pass">{tenantGroundTruthLabel(extraction.ground_truth_tenant)}</Badge>
    );
  }
  if (status === "foreign" && extraction?.catalog_tenant) {
    return (
      <Badge tone="flag">
        In {catalogTenantLabel(extraction.catalog_tenant)} catalog
      </Badge>
    );
  }
  if (!evaluation.extraction_applicable) {
    return <Badge tone="skip">Unknown document</Badge>;
  }
  return null;
}

function catalogTenantLabel(tenantId: string): string {
  return tenantShortLabel(tenantId).toLowerCase();
}

function extractionNotApplicableHint(evaluation: LiveEvaluation): string {
  const extraction = evaluation.breakdown?.extraction;
  if (extraction?.reason) return extraction.reason;
  if (extraction?.ground_truth_status === "foreign" && extraction.catalog_tenant) {
    return `This PDF is in the ${extraction.catalog_tenant} dataset, not ${extraction.tenant_id ?? "this tenant"}.`;
  }
  return "No ground truth for this document in the active tenant catalog.";
}

function ragNotApplicableHint(evaluation: LiveEvaluation): string {
  if (evaluation.is_non_po === false) {
    return "GL inherited from the purchase order — no RAG coding step.";
  }
  if (evaluation.is_non_po === true) {
    return "Non-PO run without model-proposed GL coding or retrieval context.";
  }
  return "RAG scoring did not apply to this run.";
}

function EvaluationStatusBadge({ status }: { status: EvaluationStatus }) {
  const tone =
    status === "completed"
      ? "pass"
      : status === "failed"
        ? "fail"
        : status === "running"
          ? "brand"
          : "skip";
  const label =
    status === "completed"
      ? "Evaluated"
      : status === "running"
        ? "Evaluating"
        : status === "failed"
          ? "Failed"
          : "Queued";
  return (
    <Badge tone={tone}>
      {(status === "running" || status === "pending") && (
        <span className="size-1.5 animate-pulse-soft rounded-full bg-current" aria-hidden />
      )}
      {label}
    </Badge>
  );
}

function hasWeakCategory(evaluation: LiveEvaluation): boolean {
  const scores = [
    evaluation.metrics.task_completion,
    evaluation.metrics.tool_use,
    evaluation.metrics.policy_adherence,
  ];
  if (evaluation.rag_applicable) scores.push(evaluation.metrics.rag_grounding);
  if (evaluation.extraction_applicable) {
    scores.push(evaluation.metrics.extraction_accuracy);
  }
  return scores.some((score) => score != null && score < WEAK_THRESHOLD);
}

function hasScores(...values: Array<number | null | undefined>): boolean {
  return values.some((value) => value != null);
}

function weakestLabel(
  parts: Array<{ label: string; value: number | null }>,
): { label: string; value: number } | null {
  const scored = parts.filter(
    (part): part is { label: string; value: number } => part.value != null,
  );
  if (scored.length === 0) return null;
  return scored.reduce((lowest, part) => (part.value < lowest.value ? part : lowest));
}

function toolComponents(
  evaluation: LiveEvaluation,
): Array<{ label: string; description: string; value: number | null }> {
  if (evaluation.status !== "completed") return [];
  const tools = evaluation.breakdown?.tools;
  if (!hasScores(tools?.selection, tools?.arguments)) return [];
  return [
    {
      label: "Selection",
      description: "Whether every required ERP tool was called.",
      value: tools?.selection ?? null,
    },
    {
      label: "Arguments",
      description: "Whether those calls used the right invoice identifiers.",
      value: tools?.arguments ?? null,
    },
  ];
}

function ragComponents(
  evaluation: LiveEvaluation,
): Array<{ label: string; description: string; value: number | null }> {
  if (evaluation.status !== "completed" || !evaluation.rag_applicable) return [];
  const rag = evaluation.breakdown?.rag;
  if (!hasScores(rag?.faithfulness, rag?.answer_relevancy, rag?.contextual_relevancy)) {
    return [];
  }
  return [
    {
      label: "Faithfulness",
      description: "The GL answer stayed inside retrieved policy.",
      value: rag?.faithfulness ?? null,
    },
    {
      label: "Answer relevancy",
      description: "The answer addressed the coding question.",
      value: rag?.answer_relevancy ?? null,
    },
    {
      label: "Contextual relevancy",
      description: "Retrieved chunks were useful for that question.",
      value: rag?.contextual_relevancy ?? null,
    },
  ];
}

function toolEvidence(
  evaluation: LiveEvaluation,
): { actual: string[]; expected: string[] } | undefined {
  if (evaluation.status !== "completed") return undefined;
  const tools = evaluation.breakdown?.tools;
  if (!tools) return undefined;
  if (tools.actual.length === 0 && tools.expected.length === 0) return undefined;
  return { actual: tools.actual, expected: tools.expected };
}

function apTaskChecklist(evaluation: LiveEvaluation): string[] {
  const poBacked = evaluation.is_non_po === false;
  const steps = [
    "Extract header fields, line items, and totals from the uploaded document",
    "Resolve the vendor to an ERP record",
  ];
  if (poBacked) {
    steps.push("Load the matched purchase order and goods receipt");
    steps.push("Inherit GL coding from the purchase order");
  } else {
    steps.push("Propose GL coding grounded in retrieved policy");
  }
  steps.push("Search historical bills for duplicate invoices");
  steps.push("Run deterministic policy checks (match, vendor, thresholds, math)");
  steps.push("Produce a routing decision — auto-approve, escalate for review, or hold");
  return steps;
}

function taskReading(evaluation: LiveEvaluation): string {
  if (evaluation.status === "failed") {
    return "Scoring did not finish for this run.";
  }
  if (evaluation.status !== "completed") {
    return "Judging whether this run completed the full AP exception workflow below.";
  }
  const route = evaluation.route?.replace(/_/g, " ") ?? "unknown";
  return `Score reflects how completely this run executed the workflow below and reached a defensible outcome (${route}).`;
}


function toolReading(evaluation: LiveEvaluation): string {
  if (evaluation.status === "failed") {
    return "Tool use was not scored because the judge failed.";
  }
  if (evaluation.status !== "completed") {
    return "Scoring required ERP tool selection and argument checks against the invoice branch. The headline is the lower of the two.";
  }
  const tools = evaluation.breakdown?.tools;
  const weakest = weakestLabel([
    { label: "Selection", value: tools?.selection ?? null },
    { label: "Arguments", value: tools?.arguments ?? null },
  ]);
  if (tools?.selection === 1 && tools.arguments === 0) {
    return "The required tools were called, but argument correctness scored 0%. Tool use is the lower of the two, so the headline is 0%.";
  }
  if (weakest && weakest.value < WEAK_THRESHOLD) {
    return `Held to ${Math.round(weakest.value * 100)}% by ${weakest.label.toLowerCase()}. Tool use is the lowest of selection and arguments, not an average.`;
  }
  if (weakest) {
    return "Required ERP tools were called with identifiers that match the invoice context. Graded deterministically from the ERP call trace.";
  }
  return "No tool-use components were recorded for this run.";
}

function extractionReading(evaluation: LiveEvaluation): string {
  if (evaluation.status === "failed") {
    return "Extraction accuracy was not scored because the judge failed.";
  }
  if (evaluation.status !== "completed") {
    return "Comparing extracted header fields to manifest ground truth when this run maps to a dataset case.";
  }
  if (!evaluation.extraction_applicable) {
    return extractionNotApplicableHint(evaluation);
  }
  const extraction = evaluation.breakdown?.extraction;
  if (extraction?.matches != null && extraction.total != null) {
    const tenant = extraction.ground_truth_tenant
      ? tenantGroundTruthLabel(extraction.ground_truth_tenant)
      : "manifest";
    return `Matched ${extraction.matches} of ${extraction.total} header fields against ${tenant} for case ${extraction.case_id ?? "unknown"}.`;
  }
  return "Manifest ground-truth extraction score.";
}

function policyReading(evaluation: LiveEvaluation): string {
  if (evaluation.status === "failed") {
    return "Policy adherence was not scored because the judge failed.";
  }
  if (evaluation.status !== "completed") {
    return "Scoring how the run's policy ledger compares to expectations.";
  }
  const policy = evaluation.breakdown?.policy;
  if (policy?.source === "manifest_expected_checks" && policy.matches != null && policy.total != null) {
    return `Matched ${policy.matches} of ${policy.total} expected manifest policy checks on the actual run ledger.`;
  }
  if (policy?.passed != null && policy.total != null) {
    return `Ledger pass rate: ${policy.passed} of ${policy.total} applicable checks passed (${policy.flagged ?? 0} flagged, ${policy.failed ?? 0} failed).`;
  }
  return "Deterministic score from the policy check ledger on this run.";
}

function ragReading(evaluation: LiveEvaluation): string {
  if (evaluation.status === "failed") {
    return "RAG was not scored because the judge failed.";
  }
  if (evaluation.status !== "completed") {
    return "Scoring grounded GL coding. The headline is the lowest of faithfulness, answer relevancy, and contextual relevancy.";
  }
  if (!evaluation.rag_applicable) {
    if (evaluation.is_non_po === false) {
      return "Not applicable — this is a PO-backed invoice. GL was inherited from the purchase order, so there was no RAG coding step to score.";
    }
    if (evaluation.is_non_po === true) {
      return "Not applicable — this non-PO run did not produce grounded GL coding with retrieval context (for example, it routed to human review before auto-code).";
    }
    return "Not applicable — this run did not use model-proposed GL coding with retrieval context.";
  }
  const rag = evaluation.breakdown?.rag;
  const weakest = weakestLabel([
    { label: "Faithfulness", value: rag?.faithfulness ?? null },
    { label: "Answer relevancy", value: rag?.answer_relevancy ?? null },
    { label: "Contextual relevancy", value: rag?.contextual_relevancy ?? null },
  ]);
  if (weakest && weakest.value < WEAK_THRESHOLD) {
    return `Held to ${Math.round(weakest.value * 100)}% by ${weakest.label.toLowerCase()}. RAG grounding is the weakest of the triad, not an average.`;
  }
  if (weakest) {
    return "Proposed GL coding stayed inside retrieved policy, answered the coding question, and used relevant chunks.";
  }
  return "RAG components were not recorded for this run.";
}

function scoreClass(
  value: number | null,
  options: { pending?: boolean; notApplicable?: boolean } = {},
): string {
  if (options.pending || options.notApplicable || value == null) return "text-ink-muted";
  if (value >= WEAK_THRESHOLD) return "text-pass-ink";
  if (value === 0) return "text-fail-ink";
  return "text-flag-ink";
}

function formatPercent(
  value: number | null | undefined,
  options: { pending?: boolean; notApplicable?: boolean } = {},
): string {
  if (options.pending) return "…";
  if (options.notApplicable) return "N/A";
  if (value == null) return "—";
  return `${Math.round(value * 100)}%`;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
