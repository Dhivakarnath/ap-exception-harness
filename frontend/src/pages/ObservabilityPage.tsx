import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { BasisBadge, RouteBadge, StatusBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ChevronDownIcon, ExternalIcon, ObservabilityIcon, ReviewsIcon } from "@/components/ui/icons";
import { Sparkline } from "@/components/ui/Sparkline";
import { EmptyState, Panel } from "@/components/ui/Panel";
import { cn } from "@/lib/cn";
import {
  CHECK_GLOSSARY,
  ROUTE_MIX_HELP,
  detailSections,
  formatCheckName,
  formatCost,
  formatRelativeTime,
  headlineTiles,
  metricByName,
  ROUTE_BAR_CLASS,
  ROUTE_LABELS,
  runDurationLabel,
  stpVsIndustry,
  tileValue,
  trendHasData,
  trendPoints,
} from "@/lib/operations";
import { DEEP_TRACE_STORAGE, setDeepTracePreferenceLocal } from "@/lib/deepTrace";
import { OPERATIONS_REFRESH_EVENT } from "@/lib/operationsRefresh";
import { formatMetricValue, METRIC_LABEL } from "@/lib/metrics";
import { ALL_TENANTS, apiTenantParam, tenantLabel } from "@/lib/tenants";
import { useTenantFilter } from "@/store/useTenantFilter";
import {
  fetchObservabilityStatus,
  fetchOperationsSummary,
  setDeepTracePreference,
} from "@/transport/api";
import type { ObservabilityStatus, OperationsSummary, RunSummary } from "@/types/events";
import type { RunStatus } from "@/store/runState";

/** Slow safety-net refresh; primary updates are event-driven (new runs). */
const FALLBACK_REFRESH_MS = 30 * 60 * 1000;

/**
 * Operations overview — the AP supervisor control room. Measured KPIs and
 * attention signals live here; Langfuse deep traces are optional at the bottom.
 */
export function ObservabilityPage() {
  const { tenant } = useTenantFilter();
  const tenantId = apiTenantParam(tenant);

  return (
    <PageBody className="flex flex-col gap-6">
      <OperationsContent
        tenantId={tenantId}
        tenantLabel={tenantLabel(tenant)}
        showTenant={tenant === ALL_TENANTS}
      />
    </PageBody>
  );
}

export function OperationsContent({
  tenantId,
  tenantLabel: tenantName,
  showTenant,
}: {
  tenantId: string;
  tenantLabel: string;
  showTenant: boolean;
}) {
  const navigate = useNavigate();
  const [summary, setSummary] = useState<OperationsSummary | null>(null);
  const [obsStatus, setObsStatus] = useState<ObservabilityStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const prefSynced = useRef(false);

  const load = useCallback(async (initial = false) => {
    try {
      if (initial) setLoading(true);
      else setRefreshing(true);
      const [ops, status] = await Promise.all([
        fetchOperationsSummary(tenantId),
        fetchObservabilityStatus(),
      ]);
      setSummary(ops);
      setObsStatus(status);
      setError(null);
    } catch {
      setError("Could not load operations overview");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [tenantId]);

  useEffect(() => {
    void load(true);
    const onEvent = () => void load(false);
    const onVisible = () => {
      if (document.visibilityState === "visible") void load(false);
    };
    window.addEventListener(OPERATIONS_REFRESH_EVENT, onEvent);
    document.addEventListener("visibilitychange", onVisible);
    const slow = window.setInterval(() => void load(false), FALLBACK_REFRESH_MS);
    return () => {
      window.removeEventListener(OPERATIONS_REFRESH_EVENT, onEvent);
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(slow);
    };
  }, [load]);

  const onDeepTraceToggle = async (enabled: boolean) => {
    try {
      setDeepTracePreferenceLocal(enabled);
      const status = await setDeepTracePreference(enabled);
      setObsStatus(status);
    } catch {
      /* preference stays local */
    }
  };

  useEffect(() => {
    if (prefSynced.current || !obsStatus) return;
    prefSynced.current = true;
    const stored = localStorage.getItem(DEEP_TRACE_STORAGE);
    if (stored === "1" && !obsStatus.deep_trace_preference) {
      void onDeepTraceToggle(true);
    }
  }, [obsStatus]);

  const totalRuns = metricByName(summary, "total_runs")?.value ?? 0;
  const empty = totalRuns === 0 && (summary?.recent_runs.length ?? 0) === 0;

  return (
    <>
      <PageHeader
        eyebrow="Accounts Payable"
        title="Operations overview"
        description={
          <>
            Floor health from processed invoices — scope:{" "}
            <strong className="text-ink">{tenantName}</strong>. Industry lines are
            cited benchmarks, not your measured results.
          </>
        }
        actions={
          <Button variant="secondary" onClick={() => void load(false)} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {loading && !summary ? <OperationsSkeleton /> : null}

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-fail/40 bg-fail-soft px-4 py-3 text-sm text-fail-ink"
        >
          {error}
        </div>
      )}

      {summary && (
        <>
          <SnapshotBar
            tenantName={tenantName}
            generatedAt={summary.generated_at}
            totalRuns={totalRuns}
            refreshing={refreshing}
            tracesAvailable={obsStatus?.deep_traces_available ?? false}
          />

          <HeadlineRow summary={summary} />

          <AttentionRow summary={summary} />

          {empty ? (
            <Panel title="Recent invoice runs">
              <EmptyState
                title="No processed invoices yet"
                hint="Upload a live invoice from Runs to populate this dashboard."
                action={
                  <Link
                    to="/"
                    className="text-sm font-medium text-brand-ink underline-offset-2 hover:underline"
                  >
                    Go to Runs →
                  </Link>
                }
              />
            </Panel>
          ) : (
            <RecentRunsPanel
              runs={summary.recent_runs}
              showTenant={showTenant}
              onSelect={(id) => navigate(`/runs/${id}`)}
            />
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <RouteBreakdown breakdown={summary.route_breakdown} />
            <ExceptionBreakdown checks={summary.top_exception_checks} />
          </div>

          <DetailMetrics summary={summary} />

          <DeepTracePanel status={obsStatus} onToggle={onDeepTraceToggle} />
        </>
      )}
    </>
  );
}

function OperationsSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading operations data">
      <div className="h-10 animate-pulse-soft rounded-lg border border-border bg-surface" />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-28 animate-pulse-soft rounded-xl border border-border bg-surface"
          />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className="h-24 animate-pulse-soft rounded-xl border border-border bg-surface"
          />
        ))}
      </div>
      <div className="h-64 animate-pulse-soft rounded-lg border border-border bg-surface" />
    </div>
  );
}

function SnapshotBar({
  tenantName,
  generatedAt,
  totalRuns,
  refreshing,
  tracesAvailable,
}: {
  tenantName: string;
  generatedAt: string;
  totalRuns: number;
  refreshing: boolean;
  tracesAvailable: boolean;
}) {
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface-2/50 px-4 py-2.5 text-xs"
      role="status"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-ink-muted">
        <span className="inline-flex items-center gap-2 font-medium text-ink">
          <ObservabilityIcon className="size-3.5 text-brand" />
          {tenantName}
        </span>
        <span>
          <span className="font-mono text-ink">{totalRuns.toLocaleString()}</span> runs in scope
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className={cn(
              "size-1.5 rounded-full",
              refreshing ? "animate-pulse-soft bg-brand" : "bg-skip",
            )}
            aria-hidden
          />
          {refreshing ? "Updating…" : "Updates on new runs · Refresh button anytime"}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-ink-subtle">
        <span>Snapshot {formatRelativeTime(generatedAt)}</span>
        <span aria-hidden>·</span>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 font-medium",
            tracesAvailable ? "text-pass-ink" : "text-fail-ink",
          )}
        >
          <span
            className={cn("size-1.5 rounded-full", tracesAvailable ? "bg-pass" : "bg-fail")}
            aria-hidden
          />
          {tracesAvailable ? "Deep traces ready" : "Deep traces not ready"}
        </span>
      </div>
    </div>
  );
}

function HeadlineRow({ summary }: { summary: OperationsSummary }) {
  const stpDelta = stpVsIndustry(summary);
  const showTrends = trendHasData(summary);

  return (
    <section aria-label="Headline metrics" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {headlineTiles(summary).map((tile) => {
        const series = trendPoints(summary, tile.key);
        return (
        <article
          key={tile.key}
          className="rounded-xl border border-border bg-surface p-5 shadow-sm transition-[box-shadow,transform] duration-[var(--dur-base)] hover:shadow-md motion-safe:hover:-translate-y-0.5"
        >
          <div className="flex items-start justify-between gap-2">
            <p className="text-xs font-medium text-ink-subtle">{tile.label}</p>
            <div className="flex items-center gap-2">
              {showTrends && series.length > 0 && (
                <Sparkline points={series} className="opacity-80" />
              )}
              {tile.metric && <BasisBadge basis={tile.metric.basis} />}
            </div>
          </div>
          <p className="mt-2 font-mono text-3xl font-semibold tracking-tight text-ink">
            {tile.metric ? tileValue(tile.metric) : "—"}
          </p>
          {tile.key === "touchless_rate" && stpDelta != null && (
            <p
              className={cn(
                "mt-2 text-[11px] font-medium leading-snug",
                stpDelta >= 0 ? "text-pass-ink" : "text-flag-ink",
              )}
            >
              {stpDelta >= 0 ? "+" : ""}
              {stpDelta.toFixed(0)} pts vs industry (~
              {(summary.benchmarks.touchless_rate_industry * 100).toFixed(0)}%)
            </p>
          )}
          {tile.benchmark && tile.key !== "touchless_rate" && (
            <p className="mt-2 text-[11px] leading-snug text-ink-subtle">{tile.benchmark}</p>
          )}
          {showTrends && series.filter((v) => v != null).length >= 3 && (
            <p className="mt-2 text-[10px] text-ink-subtle" title="Sparkline: this KPI per calendar day over the last two weeks">
              Daily history (last {summary.trends?.days ?? 14} days)
            </p>
          )}
        </article>
        );
      })}
    </section>
  );
}

function AttentionRow({ summary }: { summary: OperationsSummary }) {
  const { attention, eval_gate } = summary;
  const policyOk =
    eval_gate.policy_adherence != null && eval_gate.policy_adherence >= 0.8;
  const routingOk =
    eval_gate.routing_label_consistency != null &&
    eval_gate.routing_label_consistency >= 0.7;
  const gatePassing = policyOk && routingOk;

  return (
    <section aria-label="Attention" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <AttentionCard
        title="Pending reviews"
        value={String(attention.pending_reviews)}
        tone={attention.pending_reviews > 0 ? "flag" : "pass"}
        hint={attention.pending_reviews > 0 ? "Needs human decision" : "Queue clear"}
        href="/reviews"
        linkLabel="Open reviews"
        icon={<ReviewsIcon className="size-4" />}
      />
      <AttentionCard
        title="Exceptions caught"
        value={String(attention.exceptions_caught)}
        tone={attention.exceptions_caught > 0 ? "brand" : "neutral"}
        hint="Invoices where a check failed or forced human review (not pending queue count)"
        icon={<ObservabilityIcon className="size-4" />}
      />
      <AttentionCard
        title="Quality gate"
        value={
          gatePassing ? "Passing" : eval_gate.status === "unavailable" ? "—" : "Check evals"
        }
        tone={gatePassing ? "pass" : "neutral"}
        hint={
          gatePassing
            ? `Policy ${(eval_gate.policy_adherence! * 100).toFixed(0)}% · Routing ${(eval_gate.routing_label_consistency! * 100).toFixed(0)}%`
            : "Manifest smoke scorecard"
        }
        href="/evals"
        linkLabel="View evals"
      />
    </section>
  );
}

function AttentionCard({
  title,
  value,
  hint,
  tone,
  href,
  linkLabel,
  icon,
}: {
  title: string;
  value: string;
  hint: string;
  tone: "pass" | "flag" | "brand" | "neutral";
  href?: string;
  linkLabel?: string;
  icon?: ReactNode;
}) {
  const toneClass = {
    pass: "border-pass/25 bg-pass-soft/30",
    flag: "border-flag/35 bg-flag-soft/25",
    brand: "border-brand/25 bg-brand-soft/40",
    neutral: "border-border bg-surface-2/40",
  }[tone];

  const inner = (
    <>
      <div className="flex items-center gap-2">
        {icon && <span className="text-ink-subtle">{icon}</span>}
        <p className="text-xs font-medium text-ink-subtle">{title}</p>
      </div>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-ink">{value}</p>
      <p className="mt-1 text-xs text-ink-muted">{hint}</p>
      {href && linkLabel && (
        <span className="mt-3 inline-block text-xs font-medium text-brand-ink underline-offset-2 group-hover:underline">
          {linkLabel} →
        </span>
      )}
    </>
  );

  if (href) {
    return (
      <Link
        to={href}
        className={cn(
          "group block rounded-xl border p-4 transition-[box-shadow,transform] duration-[var(--dur-base)] hover:shadow-sm motion-safe:hover:-translate-y-0.5",
          toneClass,
        )}
      >
        {inner}
      </Link>
    );
  }

  return <div className={cn("rounded-xl border p-4", toneClass)}>{inner}</div>;
}

function RecentRunsPanel({
  runs,
  showTenant,
  onSelect,
}: {
  runs: RunSummary[];
  showTenant: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <Panel
      title="Recent invoice runs"
      subtitle="Newest document-processed runs"
      trailing={
        <span className="font-mono text-xs text-ink-subtle">{runs.length} shown</span>
      }
    >
      {runs.length === 0 ? (
        <EmptyState title="No runs in scope" hint="Process an invoice from the Runs page." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-[1] bg-surface">
              <tr className="border-b border-border text-left text-xs font-medium uppercase tracking-wide text-ink-subtle">
                <th scope="col" className="px-4 py-2.5">Vendor</th>
                {showTenant ? <th scope="col" className="px-4 py-2.5">Tenant</th> : null}
                <th scope="col" className="px-4 py-2.5 text-right">Total</th>
                <th scope="col" className="px-4 py-2.5">Status</th>
                <th scope="col" className="px-4 py-2.5">Route</th>
                <th scope="col" className="px-4 py-2.5 text-right">Time</th>
                <th scope="col" className="px-4 py-2.5 text-right">Cost</th>
                <th scope="col" className="px-4 py-2.5 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {runs.map((run, i) => (
                <tr
                  key={run.run_id}
                  className="cursor-pointer transition-colors hover:bg-surface-2/60 motion-safe:animate-row-in"
                  style={{ animationDelay: `${i * 40}ms` }}
                  onClick={() => onSelect(run.run_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(run.run_id);
                    }
                  }}
                  tabIndex={0}
                  role="link"
                  aria-label={`View run for ${run.vendor}`}
                >
                  <td className="px-4 py-3 font-medium text-ink">{run.vendor}</td>
                  {showTenant ? (
                    <td className="px-4 py-3 text-xs text-ink-subtle">{run.tenant_id}</td>
                  ) : null}
                  <td className="px-4 py-3 text-right font-mono tabular-nums">${run.total}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={run.status as RunStatus} />
                  </td>
                  <td className="px-4 py-3">
                    {run.route ? <RouteBadge route={run.route} /> : "—"}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-muted">
                    {runDurationLabel(run)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-ink-muted">
                    {formatCost(run.usd)}
                  </td>
                  <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => onSelect(run.run_id)}
                        className="text-xs font-medium text-brand-ink underline-offset-2 hover:underline"
                      >
                        View
                      </button>
                      {run.trace_url && (
                        <a
                          href={run.trace_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-0.5 text-xs text-ink-subtle hover:text-brand-ink"
                          title="Open deep trace in Langfuse"
                        >
                          Trace
                          <ExternalIcon className="size-3" />
                        </a>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function RouteBreakdown({ breakdown }: { breakdown: Record<string, number> }) {
  const entries = Object.entries(breakdown).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, n]) => s + n, 0) || 1;
  const max = Math.max(...entries.map(([, n]) => n), 1);

  return (
    <Panel title="Route mix" subtitle={ROUTE_MIX_HELP}>
      {entries.length === 0 ? (
        <EmptyState title="No decisions yet" hint="Routes appear after invoices are processed." />
      ) : (
        <div className="flex flex-col gap-4 p-4">
          <div
            className="flex h-3 overflow-hidden rounded-full bg-surface-3"
            role="img"
            aria-label="Route distribution stacked bar"
          >
            {entries.map(([route, count]) => (
              <div
                key={route}
                className={cn("h-full transition-all duration-500", ROUTE_BAR_CLASS[route] ?? "bg-brand/60")}
                style={{ width: `${(count / total) * 100}%` }}
                title={`${ROUTE_LABELS[route] ?? route}: ${count}`}
              />
            ))}
          </div>
          <ul className="flex flex-col gap-3">
            {entries.map(([route, count]) => (
              <li key={route}>
                <div className="mb-1 flex justify-between text-xs">
                  <span className="flex items-center gap-2 text-ink-muted">
                    <span
                      className={cn("size-2 rounded-full", ROUTE_BAR_CLASS[route] ?? "bg-brand/60")}
                      aria-hidden
                    />
                    {ROUTE_LABELS[route] ?? route}
                  </span>
                  <span className="font-mono tabular-nums text-ink-subtle">
                    {count} ({((count / total) * 100).toFixed(0)}%)
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all duration-500",
                      ROUTE_BAR_CLASS[route] ?? "bg-brand/60",
                    )}
                    style={{ width: `${(count / max) * 100}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function ExceptionBreakdown({
  checks,
}: {
  checks: Array<{ name: string; run_count: number }>;
}) {
  const max = Math.max(...checks.map((c) => c.run_count), 1);

  return (
    <Panel title="Top exception signals" subtitle="Checks that failed or forced review">
      {checks.length === 0 ? (
        <EmptyState title="No exception signals" hint="Policy checks are passing or skipping." />
      ) : (
        <ul className="flex flex-col gap-4 p-4">
          {checks.map((c) => (
            <li key={c.name}>
              <div className="mb-1 flex justify-between text-xs">
                <span className="font-medium text-ink">{formatCheckName(c.name)}</span>
                <span className="font-mono tabular-nums text-ink-subtle">
                  {c.run_count} run{c.run_count === 1 ? "" : "s"}
                </span>
              </div>
              {CHECK_GLOSSARY[c.name] && (
                <p className="mb-2 text-[11px] leading-snug text-ink-muted">
                  {CHECK_GLOSSARY[c.name]}
                </p>
              )}
              <div className="h-2 overflow-hidden rounded-full bg-surface-3">
                <div
                  className="h-full rounded-full bg-flag/80 transition-all duration-500"
                  style={{ width: `${(c.run_count / max) * 100}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function DetailMetrics({ summary }: { summary: OperationsSummary }) {
  const sections = detailSections(summary).filter((s) => s.metrics.length > 0);
  if (sections.length === 0) return null;

  return (
    <CollapsibleSection closedLabel="Show detailed metrics" openLabel="Hide detailed metrics">
      <div className="grid grid-cols-1 gap-4 border-t border-border p-4 sm:grid-cols-3">
        {sections.map((section) => (
          <div key={section.title}>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-subtle">
              {section.title}
            </h3>
            <ul className="space-y-2">
              {section.metrics.map((m) => (
                <li key={m.name} className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="text-ink-muted">{METRIC_LABEL[m.name] ?? m.name}</span>
                  <span className="flex items-center gap-2">
                    <span className="font-mono font-medium tabular-nums text-ink">
                      {formatMetricValue(m)}
                    </span>
                    <BasisBadge basis={m.basis} />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </CollapsibleSection>
  );
}

function CollapsibleSection({
  closedLabel,
  openLabel,
  children,
  className,
}: {
  closedLabel: string;
  openLabel: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <details className={cn("group rounded-xl border border-border bg-surface/50", className)}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 text-sm font-medium text-ink-muted transition-colors hover:text-ink">
        <span>
          <span className="group-open:hidden">{closedLabel}</span>
          <span className="hidden group-open:inline">{openLabel}</span>
        </span>
        <ChevronDownIcon
          className="size-4 shrink-0 text-ink-subtle transition-transform duration-[var(--dur-base)] group-open:rotate-180"
        />
      </summary>
      {children}
    </details>
  );
}

function DeepTracePanel({
  status,
  onToggle,
}: {
  status: ObservabilityStatus | null;
  onToggle: (enabled: boolean) => void;
}) {
  const available = status?.deep_traces_available ?? false;
  const langfuseProjectUrl =
    status?.langfuse_project_url ?? status?.langfuse_url ?? "http://localhost:3000";
  const pref = status?.deep_trace_preference ?? false;

  return (
    <CollapsibleSection
      closedLabel="Deep trace inspection (optional)"
      openLabel="Deep trace inspection (optional)"
      className="bg-surface-2/30"
    >
      <div className="space-y-4 border-t border-border px-4 py-4 text-sm">
        <div className="flex flex-wrap items-center gap-3">
          <StatusChip ok={available} label={available ? "Trace stack ready" : "Trace stack unavailable"} />
          {status?.otel_configured && (
            <StatusChip
              ok={status.collector_reachable}
              label={
                status.collector_reachable
                  ? "OTLP collector reachable"
                  : "OTLP configured, collector unreachable"
              }
            />
          )}
        </div>

        <p className="max-w-2xl leading-relaxed text-ink-muted">
          When enabled, new invoice runs export nested spans (model calls, per-check
          timing) to Langfuse via the OTLP collector. Uploads and demo triggers
          respect this toggle. Opens in a separate tab — Langfuse may require its own
          sign-in.
        </p>

        {status?.local_demo_hint && (
          <p className="rounded-lg bg-surface-3 px-3 py-2 text-xs text-ink-subtle">
            {status.local_demo_hint}
          </p>
        )}

        <label className="flex cursor-pointer items-center gap-3">
          <input
            type="checkbox"
            className="size-4 rounded border-border text-brand focus:ring-brand"
            checked={pref}
            disabled={!available}
            onChange={(e) => void onToggle(e.target.checked)}
          />
          <span className={available ? "text-ink" : "text-ink-subtle"}>
            Enable deep traces for new runs
          </span>
        </label>

        {!available && (
          <p className="text-xs text-ink-subtle">
            Local: run{" "}
            <code className="rounded bg-surface-3 px-1 py-0.5 font-mono">make obs-up</code> and
            ensure{" "}
            <code className="rounded bg-surface-3 px-1 py-0.5 font-mono">
              OTEL_EXPORTER_OTLP_ENDPOINT
            </code>{" "}
            is set in <code className="rounded bg-surface-3 px-1 py-0.5 font-mono">.env</code>.
          </p>
        )}

        {langfuseProjectUrl && (
          <a
            href={langfuseProjectUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-ink underline-offset-2 hover:underline"
          >
            Open Langfuse project
            <ExternalIcon className="size-3.5" />
          </a>
        )}
      </div>
    </CollapsibleSection>
  );
}

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-md border px-2 py-1 text-xs font-medium",
        ok ? "border-pass/30 bg-pass-soft/40 text-pass-ink" : "border-fail/35 bg-fail-soft/40 text-fail-ink",
      )}
    >
      <span className={cn("size-1.5 rounded-full", ok ? "bg-pass" : "bg-fail")} aria-hidden />
      {label}
    </span>
  );
}
