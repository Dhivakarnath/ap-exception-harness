import { useCallback, useEffect, useState } from "react";
import { BasisBadge } from "@/components/ui/Badge";
import { EmptyState, Panel } from "@/components/ui/Panel";
import { fetchKpis } from "@/transport/api";
import type { KpiSummary, Metric } from "@/types/events";
import { tenantLabel } from "@/lib/tenants";
import { formatMetricValue, METRIC_LABEL } from "@/lib/metrics";

const LANGFUSE_URL = import.meta.env.VITE_LANGFUSE_URL ?? "http://localhost:3000";

/**
 * The AP KPI dashboard (design §12.6). Every tile labels its number as
 * **measured** (from persisted rows) or **illustrative** (a stated assumption),
 * so a demo figure can never be misread as a validated business result — the §6
 * commitment, enforced in the UI by the `basis` field the backend sends.
 */
export function KpiDashboard({ tenantId }: { tenantId: string }) {
  const [kpis, setKpis] = useState<KpiSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setKpis(await fetchKpis(tenantId));
      setError(null);
    } catch {
      setError("Could not load KPIs");
    }
  }, [tenantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const metrics = kpis ? [...kpis.ap_kpis, ...kpis.run_metrics] : [];
  const empty = metrics.length === 0 || isAllZero(metrics);

  return (
    <Panel
      title="AP KPIs"
      subtitle={`${tenantLabel(tenantId)} · measured vs illustrative`}
      trailing={
        <a
          href={LANGFUSE_URL}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-brand-ink underline-offset-2 hover:underline"
        >
          Traces ↗
        </a>
      }
    >
      {error ? (
        <div role="alert" className="bg-fail-soft/40 px-4 py-3 text-sm text-fail-ink">
          {error}
        </div>
      ) : empty ? (
        <EmptyState
          title="No KPIs yet"
          hint="KPIs are computed from persisted document-processed runs. Upload a live invoice or switch the sidebar tenant filter."
        />
      ) : (
        <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-3">
          {metrics.map((m) => (
            <MetricTile key={m.name} metric={m} />
          ))}
        </div>
      )}
    </Panel>
  );
}

function MetricTile({ metric }: { metric: Metric }) {
  return (
    <div className="flex flex-col gap-1 bg-surface p-3">
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 text-xs text-ink-subtle">
          {METRIC_LABEL[metric.name] ?? metric.name}
        </span>
        <span className="shrink-0">
          <BasisBadge basis={metric.basis} />
        </span>
      </div>
      <span
        className="font-mono text-lg font-semibold text-ink"
        title={metric.note ?? undefined}
      >
        {formatMetricValue(metric)}
      </span>
      {metric.basis === "illustrative" && metric.note && (
        <p className="text-[11px] leading-snug text-illustrative-ink">
          {metric.note}
        </p>
      )}
    </div>
  );
}

function isAllZero(metrics: Metric[]): boolean {
  return metrics.every((m) => m.value === 0);
}
