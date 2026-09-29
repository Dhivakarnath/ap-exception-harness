import type { Metric, OperationsSummary, TrendPoint } from "@/types/events";
import { formatMetricValue, METRIC_LABEL } from "@/lib/metrics";

export function metricByName(
  summary: OperationsSummary | null,
  name: string,
): Metric | undefined {
  if (!summary) return undefined;
  const all = [...summary.kpis.run_metrics, ...summary.kpis.ap_kpis];
  return all.find((m) => m.name === name);
}

export function formatCheckName(name: string): string {
  return name.replaceAll("_", " ");
}

/** Plain-language gloss for common policy checks shown on Operations. */
export const CHECK_GLOSSARY: Record<string, string> = {
  three_way_match:
    "Compares three documents: the invoice, the purchase order (PO), and the goods receipt (GRN). All three must agree on vendor, quantities, and price before auto-approval.",
  bank_detail_change:
    "Flags when remittance or bank details changed vs the vendor master — a common fraud pattern.",
};

export const ROUTE_MIX_HELP =
  "Share of routed upload invoices in scope (matches the recent runs table).";

export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function runDurationLabel(run: {
  duration_ms?: number | null;
  started_at: string;
  finished_at: string | null;
}): string {
  if (run.duration_ms != null && run.duration_ms > 0) {
    if (run.duration_ms >= 1000) return `${(run.duration_ms / 1000).toFixed(1)} s`;
    return `${Math.round(run.duration_ms)} ms`;
  }
  if (!run.finished_at) return "—";
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(5)}`;
  return `$${usd.toFixed(4)}`;
}

export const ROUTE_LABELS: Record<string, string> = {
  auto_approve: "Auto-approved",
  route_for_approval: "Needs approval",
  hold: "Held",
  reject: "Rejected",
};

/** Tailwind fill classes for route segments in stacked bars. */
export const ROUTE_BAR_CLASS: Record<string, string> = {
  auto_approve: "bg-pass/80",
  route_for_approval: "bg-flag/80",
  hold: "bg-skip/70",
  reject: "bg-fail/80",
};

/** STP delta vs industry benchmark in percentage points (positive = ahead). */
export function stpVsIndustry(summary: OperationsSummary): number | null {
  const stp = metricByName(summary, "touchless_rate")?.value;
  if (stp == null) return null;
  return (stp - summary.benchmarks.touchless_rate_industry) * 100;
}

export function headlineTiles(summary: OperationsSummary): Array<{
  key: string;
  label: string;
  metric: Metric | undefined;
  benchmark?: string;
}> {
  const b = summary.benchmarks;
  const stp = metricByName(summary, "touchless_rate");
  const stpBench =
    stp?.value != null
      ? `Industry avg ~${(b.touchless_rate_industry * 100).toFixed(0)}%`
      : undefined;

  return [
    {
      key: "touchless_rate",
      label: "Straight-through (STP)",
      metric: stp,
      benchmark: stpBench,
    },
    {
      key: "escalation_rate",
      label: "Escalation rate",
      metric: metricByName(summary, "escalation_rate"),
    },
    {
      key: "avg_latency_ms",
      label: "Avg processing time",
      metric: metricByName(summary, "avg_latency_ms"),
      benchmark: `Exception resolution ~${b.exception_resolution_days_median}d median (industry)`,
    },
    {
      key: "cost_per_invoice_usd",
      label: "Model cost / invoice",
      metric: metricByName(summary, "cost_per_invoice_usd"),
      benchmark: "Inference only — not all-in AP cost",
    },
  ];
}

export function detailSections(summary: OperationsSummary): Array<{
  title: string;
  metrics: Metric[];
}> {
  const sections = [
    { title: "Automation", names: ["total_runs", "match_rate"] },
    { title: "Speed & cost", names: ["avg_cost_usd", "avg_context_utilisation"] },
    {
      title: "Risk & discounts",
      names: ["exceptions_caught", "early_discount_identified_rate"],
    },
  ];
  return sections.map((section) => ({
    title: section.title,
    metrics: section.names
      .map((n) => metricByName(summary, n))
      .filter((m): m is Metric => m != null),
  }));
}

export function tileValue(metric: Metric | undefined): string {
  if (!metric) return "—";
  return formatMetricValue(metric);
}

export function tileLabel(metric: Metric | undefined, fallback: string): string {
  if (!metric) return fallback;
  return METRIC_LABEL[metric.name] ?? metric.name;
}

const TREND_KEY: Record<string, keyof NonNullable<OperationsSummary["trends"]>> = {
  touchless_rate: "touchless_rate",
  escalation_rate: "escalation_rate",
  avg_latency_ms: "avg_latency_ms",
  cost_per_invoice_usd: "cost_per_invoice_usd",
};

export function trendPoints(
  summary: OperationsSummary,
  metricKey: string,
): Array<number | null> {
  const trends = summary.trends;
  if (!trends) return [];
  const seriesKey = TREND_KEY[metricKey];
  if (!seriesKey) return [];
  const series: TrendPoint[] = trends[seriesKey];
  return series.map((p) => p.value);
}

export function trendHasData(summary: OperationsSummary): boolean {
  const trends = summary.trends;
  if (!trends) return false;
  return trends.touchless_rate.some((p) => p.n > 0);
}
