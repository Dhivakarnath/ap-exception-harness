import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { OperationsContent } from "@/pages/ObservabilityPage";
import type { OperationsSummary, ObservabilityStatus } from "@/types/events";

const obsStatus: ObservabilityStatus = {
  otel_configured: true,
  collector_reachable: false,
  langfuse_configured: true,
  langfuse_reachable: false,
  langfuse_url: "http://localhost:3000",
  langfuse_project_url: "http://localhost:3000/project/ap-agent/traces",
  deep_traces_available: false,
  deep_trace_preference: false,
  local_demo_hint: "Langfuse sign-in: dev@local.dev / local-dev-password",
};

const summary: OperationsSummary = {
  tenant_id: "all",
  generated_at: new Date().toISOString(),
  kpis: {
    tenant_id: "all",
    run_metrics: [
      { name: "total_runs", value: 2, unit: "count", basis: "measured" },
      { name: "escalation_rate", value: 0, unit: "fraction", basis: "measured" },
      { name: "avg_latency_ms", value: 7800, unit: "ms", basis: "measured" },
      { name: "avg_cost_usd", value: 0.0006, unit: "usd", basis: "measured" },
    ],
    ap_kpis: [
      { name: "touchless_rate", value: 0.5, unit: "fraction", basis: "measured" },
      { name: "cost_per_invoice_usd", value: 0.0006, unit: "usd", basis: "measured" },
      { name: "exceptions_caught", value: 1, unit: "count", basis: "measured" },
      { name: "match_rate", value: 0, unit: "fraction", basis: "measured" },
      { name: "early_discount_identified_rate", value: 0, unit: "fraction", basis: "measured" },
    ],
  },
  benchmarks: {
    touchless_rate_industry: 0.354,
    touchless_rate_source: "Ardent",
    exception_resolution_days_median: 4,
    exception_resolution_source: "APQC",
  },
  attention: { pending_reviews: 0, exceptions_caught: 1 },
  route_breakdown: { auto_approve: 1, hold: 1 },
  top_exception_checks: [{ name: "three_way_match", run_count: 1 }],
  eval_gate: {
    status: "available",
    policy_adherence: 1,
    routing_label_consistency: 1,
  },
  trends: {
    days: 14,
    touchless_rate: [
      { day: "2026-09-19", value: 0.4, n: 1 },
      { day: "2026-09-20", value: 0.5, n: 1 },
    ],
    escalation_rate: [
      { day: "2026-09-19", value: 0, n: 1 },
      { day: "2026-09-20", value: 0, n: 1 },
    ],
    avg_latency_ms: [
      { day: "2026-09-19", value: 7000, n: 1 },
      { day: "2026-09-20", value: 7800, n: 1 },
    ],
    cost_per_invoice_usd: [
      { day: "2026-09-19", value: 0.0005, n: 1 },
      { day: "2026-09-20", value: 0.0006, n: 1 },
    ],
  },
  recent_runs: [
    {
      run_id: "run-1",
      tenant_id: "retail-demo",
      invoice_id: "inv-1",
      scenario: "upload",
      vendor: "Acme Corp",
      total: "500",
      status: "completed",
      route: "auto_approve",
      gl_account: null,
      input_tokens: 100,
      output_tokens: 20,
      usd: 0.0006,
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
    },
  ],
};

vi.mock("@/transport/api", () => ({
  fetchOperationsSummary: vi.fn(),
  fetchObservabilityStatus: vi.fn(),
  setDeepTracePreference: vi.fn(),
}));

import {
  fetchObservabilityStatus,
  fetchOperationsSummary,
} from "@/transport/api";

describe("OperationsContent", () => {
  beforeEach(() => {
    vi.mocked(fetchOperationsSummary).mockResolvedValue(summary);
    vi.mocked(fetchObservabilityStatus).mockResolvedValue(obsStatus);
  });

  it("renders headline metrics and recent runs", async () => {
    render(
      <MemoryRouter>
        <OperationsContent tenantId="all" tenantLabel="All tenants" showTenant={false} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Operations overview")).toBeInTheDocument();
    });

    expect(screen.getByText("Straight-through (STP)")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getAllByText("Deep trace inspection (optional)").length).toBeGreaterThan(0);
  });
});
