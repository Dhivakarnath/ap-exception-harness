import type { Metric } from "@/types/events";

/**
 * Shared metric presentation: the human labels for the backend's metric names
 * and a single value formatter keyed on unit. Both the compact KpiStrip and the
 * full KpiDashboard use these so a metric is always labelled and formatted the
 * same way wherever it appears.
 */
export const METRIC_LABEL: Record<string, string> = {
  total_runs: "Total runs",
  decided_invoices: "Decided invoices",
  escalation_rate: "Escalation rate",
  avg_latency_ms: "Avg latency",
  avg_cost_usd: "Avg cost / run",
  avg_context_utilisation: "Context used",
  touchless_rate: "Touchless / STP",
  cost_per_invoice_usd: "Cost / invoice",
  match_rate: "Three-way match rate",
  exceptions_caught: "Exceptions caught",
  early_discount_identified_rate: "Early-discount capture",
  cycle_time_reduction: "Cycle-time reduction",
};

export function formatMetricValue(m: Metric): string {
  if (m.value === null || m.basis === "pending") {
    return "—";
  }
  switch (m.unit) {
    case "fraction":
      return `${(m.value * 100).toFixed(0)}%`;
    case "usd":
      return m.value < 0.01 && m.value > 0
        ? `$${m.value.toFixed(6)}`
        : `$${m.value.toFixed(4)}`;
    case "ms":
      return m.value >= 1000
        ? `${(m.value / 1000).toFixed(1)} s`
        : `${m.value.toFixed(0)} ms`;
    case "count":
      return m.value.toLocaleString();
    default:
      return String(m.value);
  }
}
