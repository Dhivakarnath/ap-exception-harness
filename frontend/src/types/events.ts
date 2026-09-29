/**
 * The RunEvent wire contract — the single normalized envelope the backend emits
 * over SSE (design §12.3). These types mirror the backend exactly, including its
 * snake_case field names (see ap_agent/observability/events.py `as_wire` and
 * sse.py `replay_events_from_db`), so the transport never guesses a shape.
 *
 * A payload is typed per channel via a discriminated union on `channel`, so the
 * reducer gets a precisely-typed payload after a single switch.
 */

export type EventChannel =
  | "stage"
  | "check"
  | "tool"
  | "extraction"
  | "decision"
  | "hitl"
  | "cost"
  | "error";

export type Verdict = "pass" | "flag" | "fail" | "skip";

export type Route =
  | "auto_approve"
  | "hold"
  | "route_for_approval"
  | "reject";

export type Actor = "agent" | "human" | "policy_engine";

/** channel = "stage": a supervisor node delta (start/finish). */
export interface StagePayload {
  name: string;
  status: string;
  duration_ms?: number | null;
}

/** channel = "check": one deterministic check, as evaluated. */
export interface CheckPayload {
  name: string;
  category: string;
  verdict: Verdict;
  severity: string;
  reasoning: string;
  threshold?: string | number | null;
  actual?: string | number | null;
  forces_review?: boolean;
  citations?: string[];
  inputs?: Record<string, unknown> | null;
  /** Per-check wall time when the emitter measured it; null on the scripted
   *  deterministic backbone, which evaluates the whole ledger in one pass. */
  duration_ms?: number | null;
}

/** channel = "tool": an ERP (or other) tool call, args PII-redacted server-side. */
export interface ToolPayload {
  name: string;
  args?: Record<string, unknown> | null;
  result_summary?: string | null;
}

/** channel = "extraction": canonical extracted fields (values snapshot). */
export interface ExtractionPayload {
  fields?: ExtractionField[];
  /** The extracted line table — description/qty/unit price/line total/unit/sku,
   *  each with its own confidence and (when located) source region. Absent when
   *  the invoice is header-only or on the scripted demo path. */
  line_items?: ExtractionLineItem[];
  /** How the fields were read — the Docling parse + Bedrock extraction story.
   *  Absent on the scripted demo path (a pre-supplied invoice, no real parse). */
  meta?: ExtractionMeta | null;
}

/** One extracted invoice line, with per-line confidence and source region. */
export interface ExtractionLineItem {
  line_number: number;
  description: string;
  quantity: string;
  unit_price: string;
  line_total: string;
  unit_of_measure?: string | null;
  sku?: string | null;
  confidence?: number | null;
  region_ref?: string | null;
  page?: number | null;
  bbox?: BBox | null;
}

/**
 * The extraction *process* metadata: the two real stages behind the fields.
 * All optional — a value is simply not shown when the backend didn't record it.
 */
export interface ExtractionMeta {
  // Docling parse stage.
  parse_strategy?: string | null;
  escalated_to_ocr?: boolean | null;
  parser_name?: string | null;
  parser_version?: string | null;
  page_count?: number | null;
  text_length?: number | null;
  parse_duration_ms?: number | null;
  // Bedrock extraction stage.
  model_id?: string | null;
  prompt_version?: string | null;
  images_attached?: number | null;
  attempts_used?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  extract_duration_ms?: number | null;
}

/** A normalised bounding box (fractions of the page, top-left origin). */
export interface BBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface ExtractionField {
  name: string;
  value: string | number | null;
  confidence?: number | null;
  region_ref?: string | null;
  /** 1-based page the field was recovered from, when known. */
  page?: number | null;
  /** The field's box on the page for highlight-back; null when no region was
   *  recovered (e.g. synthetic demo invoices carry no geometry). */
  bbox?: BBox | null;
}

/** channel = "decision": the terminal route + why + on whose authority. */
export interface DecisionPayload {
  route: Route;
  rationale: string;
  actor: Actor;
  citations?: string[];
  gl_account?: string | null;
}

/** channel = "hitl": a pause for a human, with the allowed decisions. */
export interface HitlPayload {
  status: string;
  channel?: string;
  required_tier?: string | null;
  allowed_decisions?: string[];
  case_summary?: string | null;
}

/** channel = "cost": token + USD accounting for the run. */
export interface CostPayload {
  input_tokens: number;
  output_tokens: number;
  usd: number;
}

/** channel = "error": a structured, unsoftened failure. */
export interface ErrorPayload {
  error_type?: string;
  message: string;
  stage?: string | null;
  trace_id?: string | null;
  retryable?: boolean;
}

/** The channel-to-payload map, so a RunEvent is a discriminated union. */
export interface ChannelPayloads {
  stage: StagePayload;
  check: CheckPayload;
  tool: ToolPayload;
  extraction: ExtractionPayload;
  decision: DecisionPayload;
  hitl: HitlPayload;
  cost: CostPayload;
  error: ErrorPayload;
}

/** One event on the wire. `RunEvent` is the union across all channels. */
export type RunEvent = {
  [C in EventChannel]: {
    run_id: string;
    trace_id: string | null;
    seq: number;
    channel: C;
    at: string | null;
    payload: ChannelPayloads[C];
  };
}[EventChannel];

/** A run summary from `GET /runs` (the InvoiceQueue read side). */
export interface RunSummary {
  run_id: string;
  tenant_id: string;
  invoice_id: string;
  scenario: string;
  vendor: string;
  total: string;
  status: "running" | "completed" | "failed" | "awaiting_review";
  route: Route | null;
  gl_account: string | null;
  input_tokens: number;
  output_tokens: number;
  usd: number;
  started_at: string;
  finished_at: string | null;
  pending_review?: boolean;
  /** The fixture stem whose rendered page represents this run's document. */
  document_stem?: string | null;
  trace_id?: string | null;
  trace_url?: string | null;
  duration_ms?: number | null;
}

/** A single metric from the KPI endpoint (`{name,value,unit,basis,note}`). */
export type MetricBasis = "measured" | "illustrative" | "pending";

export interface Metric {
  name: string;
  value: number | null;
  unit: string;
  basis: MetricBasis;
  note?: string | null;
}

/** `GET /tenants/{id}/kpis` -> `summarise(...)`. */
export interface KpiSummary {
  tenant_id: string;
  run_metrics: Metric[];
  ap_kpis: Metric[];
}

/** `GET /tenants/{id}/operations-summary` — Operations control room. */
export interface OperationsBenchmarks {
  touchless_rate_industry: number;
  touchless_rate_source: string;
  exception_resolution_days_median: number;
  exception_resolution_source: string;
}

export interface TrendPoint {
  day: string;
  value: number | null;
  n: number;
}

export interface OperationsTrends {
  days: number;
  touchless_rate: TrendPoint[];
  escalation_rate: TrendPoint[];
  avg_latency_ms: TrendPoint[];
  cost_per_invoice_usd: TrendPoint[];
}

export interface OperationsSummary {
  tenant_id: string;
  generated_at: string;
  kpis: KpiSummary;
  benchmarks: OperationsBenchmarks;
  trends?: OperationsTrends;
  attention: {
    pending_reviews: number;
    exceptions_caught: number;
  };
  route_breakdown: Record<string, number>;
  top_exception_checks: Array<{ name: string; run_count: number }>;
  eval_gate: {
    status: string;
    policy_adherence: number | null;
    routing_label_consistency: number | null;
    generated_at?: string | null;
  };
  recent_runs: RunSummary[];
}

/** `GET /platform/observability/status` */
export interface ObservabilityStatus {
  otel_configured: boolean;
  collector_reachable: boolean;
  langfuse_configured: boolean;
  langfuse_reachable?: boolean;
  langfuse_url: string | null;
  langfuse_project_url?: string | null;
  deep_traces_available: boolean;
  deep_trace_preference: boolean;
  local_demo_hint: string | null;
}

/* ---- Platform config endpoints (design §12) ---- */

/** `GET /policy` — the active policy pack, as the agent applies it. */
export interface PolicyPack {
  tenant_id: string;
  version: string;
  industry: string;
  description: string;
  base_currency: string;
  tolerances: {
    price_pct: string;
    quantity_pct: string;
    total_absolute: string;
    allow_partial_delivery: boolean;
    allow_overbilling: boolean;
  };
  thresholds: {
    touchless_max: string;
    min_confidence_for_touchless: number;
    min_confidence_for_gl_coding: number;
    threshold_avoidance_band_pct: string;
    new_vendor_days: number;
  };
  doa: { bands: DOABand[] };
  sod: Record<string, boolean>;
  duplicates: {
    lookback_days: number;
    fuzzy_number_similarity: number;
    fuzzy_amount_tolerance: string;
    fuzzy_date_window_days: number;
  };
  payment_terms: {
    default_terms: string;
    capture_discounts: boolean;
    min_discount_annualised_pct: string;
  };
  require_grn: boolean;
  allow_non_po_invoices: boolean;
  identity: string;
  doa_boundaries: string[];
}

export interface DOABand {
  tier: string;
  max_amount: string | null;
  roles: string[];
}

/** `GET /guardrails` — the three-layer defense config. */
export interface GuardrailsConfig {
  layers: GuardrailLayer[];
  tokens: Record<string, string[]>;
  no_payment_capability: boolean;
  note: string;
}

export interface GuardrailLayer {
  id: string;
  layer: number;
  name: string;
  where: string;
  enforces: string;
  blocks_example: string;
}

export type EvaluationStatus = "pending" | "running" | "completed" | "failed";

export interface EvaluationBreakdown {
  task?: {
    score: number | null;
    reason: string | null;
  };
  tools: {
    selection: number | null;
    arguments: number | null;
    actual: string[];
    expected: string[];
    reason?: string | null;
    mismatches?: string[];
  };
  rag: {
    faithfulness: number | null;
    answer_relevancy: number | null;
    contextual_relevancy: number | null;
  };
  extraction: {
    matches: number | null;
    total: number | null;
    mismatches: string[];
    case_id: string | null;
    source: string | null;
    ground_truth_status?: "matched" | "unknown" | "foreign" | null;
    ground_truth_tenant?: string | null;
    catalog_tenant?: string | null;
    catalog_case_id?: string | null;
    tenant_id?: string | null;
    reason?: string | null;
  };
  policy: {
    matches: number | null;
    total: number | null;
    passed: number | null;
    flagged: number | null;
    failed: number | null;
    mismatches: string[];
    case_id: string | null;
    source: string | null;
  };
}

export interface LiveEvaluation {
  run_id: string;
  tenant_id: string;
  invoice_id: string;
  invoice_number: string;
  vendor: string;
  /** False = PO-backed; true = non-PO; null when invoice row is missing. */
  is_non_po: boolean | null;
  route: Route | null;
  status: EvaluationStatus;
  metrics: {
    task_completion: number | null;
    tool_use: number | null;
    rag_grounding: number | null;
    extraction_accuracy: number | null;
    policy_adherence: number | null;
  };
  breakdown?: EvaluationBreakdown;
  rag_applicable: boolean;
  extraction_applicable: boolean;
  policy_applicable: boolean;
  evaluated_after_hitl: boolean;
  judge_model: string;
  error: string | null;
  started_at: string | null;
  evaluated_at: string | null;
  created_at: string;
}

/** Manifest CI scorecard from `evals/results.json` (`make eval` / `make eval-live`). */
export interface ManifestScorecard {
  status: "not_run" | "available";
  message?: string;
  suite?: string;
  generated_at?: string;
  note?: string;
  policy_adherence: number | null;
  routing_label_consistency: number | null;
  pipeline_cases_passed?: number;
  pipeline_cases_total?: number;
  live?: {
    task_completion: number | null;
    tool_correctness: number | null;
    argument_correctness: number | null;
    rag_faithfulness: number | null;
    rag_answer_relevancy: number | null;
    rag_contextual_relevancy: number | null;
    extraction_accuracy: number | null;
  };
}

/** `GET /evals` — manifest gate + per-upload DeepEval results. */
export interface EvalsReport {
  status: "not_run" | "available";
  message?: string;
  manifest_scorecard?: ManifestScorecard;
  evaluations: LiveEvaluation[];
}
