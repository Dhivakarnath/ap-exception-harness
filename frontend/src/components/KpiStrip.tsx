import { useEffect, useState } from "react";
import { BasisBadge } from "@/components/ui/Badge";
import { fetchKpis } from "@/transport/api";
import type { KpiSummary, Metric } from "@/types/events";
import { formatMetricValue, METRIC_LABEL } from "@/lib/metrics";

/**
 * A compact, at-a-glance KPI strip for the Runs landing page. It surfaces a
 * curated handful of headline metrics (total runs, touchless rate, escalation
 * rate, avg cost). The full, exhaustive scorecard lives on the Observability
 * Operations page — this is the summary, not the source of truth.
 */
const HEADLINE = [
  "total_runs",
  "touchless_rate",
  "escalation_rate",
  "avg_cost_usd",
] as const;

export function KpiStrip({ tenantId }: { tenantId: string }) {
  const [kpis, setKpis] = useState<KpiSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchKpis(tenantId)
      .then((k) => {
        if (!cancelled) setKpis(k);
      })
      .catch(() => {
        /* strip simply stays hidden if KPIs can't load */
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  if (!kpis) return null;

  const all = [...kpis.run_metrics, ...kpis.ap_kpis];
  const byName = new Map(all.map((m) => [m.name, m]));
  const tiles = HEADLINE.map((n) => byName.get(n)).filter(
    (m): m is Metric => m != null,
  );

  if (tiles.length === 0) return null;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {tiles.map((m) => (
        <div
          key={m.name}
          className="rounded-lg border border-border bg-surface p-4 shadow-sm"
        >
          <div className="flex items-start justify-between gap-2">
            <span className="text-xs text-ink-subtle">
              {METRIC_LABEL[m.name] ?? m.name}
            </span>
            <BasisBadge basis={m.basis} />
          </div>
          <div
            className="mt-2 font-mono text-2xl font-semibold text-ink"
            title={m.note ?? undefined}
          >
            {formatMetricValue(m)}
          </div>
        </div>
      ))}
    </div>
  );
}
