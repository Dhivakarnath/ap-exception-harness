/**
 * REST client for the AP Exception Agent API.
 *
 * In dev, requests go through Vite's `/api` proxy to the backend on :8080 (see
 * vite.config.ts), so the browser stays same-origin for XHR. `VITE_API_BASE`
 * overrides the base for a real deployment. The SSE stream is handled separately
 * (transport/stream.ts) because EventSource needs an absolute-or-proxied URL and
 * its own lifecycle.
 */

import type {
  EvalsReport,
  GuardrailsConfig,
  KpiSummary,
  ObservabilityStatus,
  OperationsSummary,
  PolicyPack,
  Route,
  RunEvent,
  RunSummary,
} from "@/types/events";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export interface TriggerRequest {
  scenario: string;
  live?: boolean;
  mcp?: boolean;
  /** Overrides the global deep-trace preference when set. */
  deep_trace?: boolean;
}

export interface TriggerResponse {
  run_id: string;
  stream_url: string;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Start a run. Returns its id + the relative stream URL. */
export async function triggerRun(req: TriggerRequest): Promise<TriggerResponse> {
  const res = await fetch(`${API_BASE}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    throw new ApiError(res.status, `POST /runs failed: ${res.status}`);
  }
  return (await res.json()) as TriggerResponse;
}

/** The InvoiceQueue read side: runs this backend process has handled. */
export function listRuns(tenant = "all"): Promise<RunSummary[]> {
  const qs = new URLSearchParams({ tenant });
  return getJson<RunSummary[]>(`/runs?${qs.toString()}`);
}

/** A single run's summary (for the run-detail header + document mapping). */
export function fetchRun(runId: string): Promise<RunSummary> {
  return getJson<RunSummary>(`/runs/${encodeURIComponent(runId)}`);
}

/** The URL of a fixture's rendered first-page PNG (for the document viewer). */
export function documentImageUrl(stem: string): string {
  return `${API_BASE}/documents/${encodeURIComponent(stem)}/image`;
}

/** The URL of an upload run's own rendered page (the real file the user sent). */
export function documentImageUrlForRun(runId: string): string {
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/document/image`;
}

/**
 * Upload a real invoice file and run it through the live pipeline: a genuine
 * Docling parse and a live Bedrock extraction, not a scripted demo invoice.
 * Multipart, not JSON — the file is the whole point. Returns the same
 * `{run_id, stream_url}` shape as `triggerRun`, so callers navigate the same
 * way regardless of which trigger started the run.
 */
export async function uploadRun(
  file: File,
  opts: {
    mcp?: boolean;
    tenant?: string;
    allowDuplicate?: boolean;
    deepTrace?: boolean;
    onUploadProgress?: (percent: number) => void;
    onPhase?: (phase: "uploading" | "processing") => void;
  } = {},
): Promise<TriggerResponse> {
  const form = new FormData();
  form.append("file", file);
  if (opts.mcp) form.append("mcp", "true");
  if (opts.tenant) form.append("tenant", opts.tenant);
  if (opts.allowDuplicate) form.append("allow_duplicate", "true");
  if (opts.deepTrace !== undefined) form.append("deep_trace", opts.deepTrace ? "true" : "false");

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/runs/upload`);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.responseType = "json";

    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round((event.loaded / event.total) * 100);
      opts.onUploadProgress?.(percent);
    });

    xhr.upload.addEventListener("loadstart", () => {
      opts.onPhase?.("uploading");
    });

    xhr.upload.addEventListener("load", () => {
      opts.onPhase?.("processing");
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as TriggerResponse);
        return;
      }
      let detail = `${xhr.status}`;
      const body = xhr.response as { error?: string } | null;
      if (body?.error) detail = body.error;
      reject(new ApiError(xhr.status, detail));
    });

    xhr.addEventListener("error", () => {
      reject(new ApiError(0, "Network error while uploading the document"));
    });

    xhr.addEventListener("abort", () => {
      reject(new ApiError(0, "Upload cancelled"));
    });

    xhr.send(form);
  });
}

export interface ReviewRequest {
  decision: "approve" | "edit" | "reject";
  approver_identity?: string;
  note?: string;
  edited_gl_account?: string;
  edited_cost_center?: string;
}

export interface ReviewResponse {
  run_id: string;
  route: Route | null;
}

/** Resume a paused (awaiting-review) run with the human's decision. */
export async function submitReview(
  runId: string,
  req: ReviewRequest,
): Promise<ReviewResponse> {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { error?: string };
      if (body.error) detail = body.error;
    } catch {
      /* keep the status code */
    }
    throw new ApiError(res.status, `Review failed: ${detail}`);
  }
  return (await res.json()) as ReviewResponse;
}

/** A tenant's KPI + run-metric summary for the dashboard. */
export function fetchKpis(tenantId: string): Promise<KpiSummary> {
  return getJson<KpiSummary>(`/tenants/${encodeURIComponent(tenantId)}/kpis`);
}

/** Operations control-room payload (KPIs, attention, breakdowns, recent runs). */
export function fetchOperationsSummary(tenantId: string): Promise<OperationsSummary> {
  return getJson<OperationsSummary>(
    `/tenants/${encodeURIComponent(tenantId)}/operations-summary`,
  );
}

/** Deep-trace stack health (Langfuse / OTLP collector). */
export function fetchObservabilityStatus(): Promise<ObservabilityStatus> {
  return getJson<ObservabilityStatus>("/platform/observability/status");
}

export async function setDeepTracePreference(enabled: boolean): Promise<ObservabilityStatus> {
  const res = await fetch(`${API_BASE}/platform/observability/deep-trace`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, "Failed to update deep-trace preference");
  }
  return (await res.json()) as ObservabilityStatus;
}

/** The active policy pack for the Policy page. */
export function fetchPolicy(tenantId: string): Promise<PolicyPack> {
  return getJson<PolicyPack>(`/policy?tenant=${encodeURIComponent(tenantId)}`);
}

/** The three-layer guardrail configuration for the Guardrails page. */
export function fetchGuardrails(): Promise<GuardrailsConfig> {
  return getJson<GuardrailsConfig>("/guardrails");
}

/** The eval scorecard (or an honest not-run state) for the Evals page. */
export function fetchEvals(tenant = "all"): Promise<EvalsReport> {
  const qs = new URLSearchParams({ tenant });
  return getJson<EvalsReport>(`/evals?${qs.toString()}`);
}

export interface HitlReviewSummary {
  id: string;
  run_id: string;
  tenant_id: string;
  invoice_id: string;
  reason: string;
  required_tier: string | null;
  channel: string;
  status: string;
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  gl_account: string | null;
}

/** Durable HITL queue from Postgres (survives API restarts). */
export function listHitlReviews(
  tenantId: string,
  status = "pending",
): Promise<HitlReviewSummary[]> {
  const q = new URLSearchParams({ tenant: tenantId, status });
  return getJson<HitlReviewSummary[]>(`/hitl/reviews?${q.toString()}`);
}

/**
 * Fetch a completed run's events via the replay path (one batch). Used by the
 * store to reconcile after a seq gap, and to render a finished run. The endpoint
 * returns SSE frames even for replay, so we parse them the same way the live
 * transport does.
 */
export async function fetchRunEventsSnapshot(
  runId: string,
): Promise<RunEvent[]> {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}/events`, {
    headers: { Accept: "text/event-stream" },
  });
  if (!res.ok) {
    throw new ApiError(res.status, `GET /runs/${runId}/events failed`);
  }
  const text = await res.text();
  return parseSseFrames(text);
}

/** Parse a block of SSE text into RunEvents (data: lines only). */
export function parseSseFrames(text: string): RunEvent[] {
  const events: RunEvent[] = [];
  for (const line of text.split("\n")) {
    const trimmed = line.trimStart();
    if (trimmed.startsWith("data:")) {
      const json = trimmed.slice("data:".length).trim();
      if (json) {
        try {
          events.push(JSON.parse(json) as RunEvent);
        } catch {
          // A malformed frame is skipped rather than crashing the parse; the
          // seq-gap check downstream will notice if anything was actually lost.
        }
      }
    }
  }
  return events;
}
