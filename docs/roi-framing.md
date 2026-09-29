# Business impact — measured results vs industry benchmarks

This document is the **business impact layer** for the Agentic AP Exception
Handling platform. It pairs **numbers this deployment produces** (Postgres audit
tables, eval scorecard) with **industry benchmarks** (cited third-party surveys)
so a buyer or reviewer can see *what we proved* and *how the market compares*.

**Legend (used throughout portfolio docs):**

| Label | Meaning |
|-------|---------|
| **Measured (this deployment)** | Recomputable from `runs`, `decisions`, `check_results`, or `backend/evals/results.json` |
| **Industry benchmark (cited)** | Third-party survey or open-standards benchmark — not a claim this product achieved at a named customer |
| **Modeled impact** | Arithmetic from stated inputs (volume, labor rate) — scenario only |

Refresh operational numbers: `make roi-metrics` (Postgres required).

Update the [revision history](#revision-history) when metrics or benchmarks change.

---

## Product scope (what we automate)

This platform automates the **exception-handling slice** of AP:

1. Ingest and extract invoice data (PDF/image upload; v2: email, webhook, bucket).
2. Run deterministic policy checks and PO/non-PO routing.
3. Decide auto-approve, route for approval, hold, or reject — with audit trail and HITL.
4. Post the decision to ERP via MCP (mock in v1; swappable for production ERP).

It does **not** execute payments. Full invoice-to-pay cycle time in industry
surveys includes mailroom, approver queues, and payment runs — we compare only
the **automation step** unless noted.

---

## Industry benchmarks (cited)

| Metric | Benchmark | Source |
|--------|-----------|--------|
| Average invoice processing time (receipt → payment-ready) | **8.2 days** (2025); **9.2 days** (2024) | [Ardent Partners, *State of ePayables 2025*](https://www.bottomline.com/cdn/1517/5157/1685/Ardent%5FPartners%5F-%5FState%5Fof%5FePayables%5F2025%5F-%5FBottomline%5F-%5FFINAL.pdf) |
| Straight-through (touchless) processing | **~35.4%** (2025); **~32.6%** (2024); best-in-class **~49%** | Same |
| All-in cost per invoice | **~$9.84** (2025); **~$9.40** (2024) | Same |
| Advanced automation users — processing time | **~2.9 days** vs **8.2 days** industry average | [Medius summary of Ardent 2025](https://www.medius.com/resources/guides-reports/ardent-partners-state-of-epayables/) |
| Cycle time to resolve an invoice **error** (calendar days) | **Median 4.0 days** | [APQC OSB measure 100632](https://www.apqc.org/resources/benchmarking/open-standards-benchmarking/measures/cycle-time-days-resolve-invoice-error) |
| Cycle time from exception detected → resolved (working days) | **Median 5.0 days** | [APQC OSB measure](https://www.apqc.org/what-we-do/benchmarking/open-standards-benchmarking/measures/average-cycle-time-working-days-when) |
| Manual exception investigation (active work, per exception) | **~15–45 minutes** (vendor synthesis citing IOFM/APQC patterns) | [Nexus AP processing benchmarks](https://www.nexusap.com/research/invoice-processing-time-benchmarks) |

These frame the **problem and addressable opportunity**. They are not substituted
for measured scores from this build.

---

## Measured (this deployment)

### Quality gate — adversarial eval harness

**Deterministic CI (`make eval`, smoke — 30 failure modes, one case per mode):**

| Metric | Latest (2026-09-16) | Gate | Basis |
|--------|---------------------|------|-------|
| Policy adherence | **100%** (30/30) | ≥ 80% | Measured |
| Routing label consistency | **100%** (30/30) | ≥ 70% | Measured |

**Full quick dataset (90 adversarial cases):** **100%** policy adherence after
INC-026 alignment. Manifest route mix (by design): 15 auto-approve, 42
route-for-approval, 27 hold, 6 reject — exception-heavy, not representative of
steady-state invoice mix.

**Live Bedrock sample (`make eval-live --smoke`):**

| Metric | Latest | Gated? | Basis |
|--------|--------|--------|-------|
| Task completion | 95% | No | Measured (small N) |
| Tool correctness | 100% | No | Measured |
| Argument correctness | 50% (2 agent cases) | No | Measured (volatile at N=2) |
| RAG triad | 100% | No | Measured |
| Extraction accuracy | 100% (5 cases) | No | Measured |

Artifact: `backend/evals/results.json`.

### Operational KPIs — persisted runs

From `make roi-metrics` snapshot **2026-09-18** (all tenants; document-processed
runs with extraction provenance):

| Metric | Value | Basis |
|--------|-------|-------|
| Document-processed runs | 2 | Measured |
| `avg_latency_ms` (all routes) | **7,801 ms** (~7.8 s) | Measured |
| `auto_approve` path — p50 latency | **10,198 ms** (~10.2 s) | Measured (n=1) |
| `hold` path — p50 latency | **5,404 ms** (~5.4 s) | Measured (n=1) |
| `touchless_rate` | **50%** (1/2 decided) | Measured |
| `cost_per_invoice_usd` (model) | **$0.000668** | Measured |
| `exceptions_caught` | 1 | Measured |

**Note:** Upload/live-run volume on this deployment is still small (n=2). After
pilot ingestion (`make parse-demo`, live uploads, or customer connector in v2),
re-run `make roi-metrics` for production-scale percentiles. Eval harness scores
above are the primary quality proof until volume grows.

### Definitions (dashboard parity)

| Metric | Unit | Source |
|--------|------|--------|
| `total_runs` | count | Document-processed runs with extraction provenance |
| `escalation_rate` | fraction | `route_for_approval` / all decided |
| `avg_latency_ms` | ms | Mean `Run.duration_ms` over completed runs |
| `avg_cost_usd` | USD | Mean token cost per run |
| `touchless_rate` | fraction | `auto_approve` / decided (STP proxy) |
| `match_rate` | fraction | `three_way_match` PASS / evaluated (non-SKIP) |
| `exceptions_caught` | count | Runs with FAIL or review-forcing checks |

---

## Benchmark comparison (scoped)

Compare **like with like**: industry exception **resolution elapsed time** and
**active clerk time** vs our **automated decision latency** on touchless paths.

| Dimension | Industry benchmark (cited) | This platform (measured) | Scope |
|-----------|---------------------------|--------------------------|--------|
| Exception resolution (elapsed) | **4–5 days** median (APQC) | **~10 s** p50 on `auto_approve` upload run | Automation path only; excludes human approval queue |
| Active exception investigation | **~30 min** midpoint of 15–45 min range | **~10 s** same run | Processing time, not calendar wait |
| Full invoice processing | **8.2 days** average (Ardent 2025) | Not in scope — we terminate at approve/hold/reject | See product scope |
| Straight-through rate | **~35%** industry average | **50%** on n=2 uploads; eval manifest is adversarial | Upload sample vs industry steady-state |
| Model cost per invoice | **~$9.84** all-in industry | **~$0.0007** model inference | Different cost bases — do not net without labor model |

### Processing-time reduction (touchless path)

For invoices that **auto-approve** on this deployment:

```
Industry active work (midpoint)  ≈ 30 minutes
Measured auto_approve latency    ≈ 10.2 seconds ≈ 0.17 minutes

Processing-time reduction ≈ 1 − (0.17 / 30) ≈ 99.4%
```

For **elapsed calendar** exception resolution (APQC median **4 days** ≈ 5,760
minutes of clock time, much of it queue/wait):

```
Elapsed reduction (automation slice only) ≈ 1 − (0.17 / 5760) ≈ 99.99%
```

These reductions apply to the **automated decision step** on touchless cases
only. HITL routes still incur human wait; industry **approval routing** averages
multiple days in process breakdowns (see Ken from Finance / Ardent analyses).

---

## Modeled impact (scenario — not measured ROI)

Illustrative **annual value** for a mid-market pilot. Adjust inputs per customer.

| Input | Value | Basis |
|-------|-------|-------|
| Invoices / month | 5,000 | Scenario |
| Exception rate | 22% | Industry average (Ken from Finance) |
| Exceptions / month | 1,100 | Derived |
| Share auto-resolved by platform | 60% | Scenario (between Nexus AP “60–70%” and current eval capability) |
| Minutes saved per auto-resolved exception | 25 | Midpoint of 15–45 min industry range |
| Fully loaded AP clerk rate | $45 / hour | Scenario (US mid-market) |

```
Labor hours saved / month = 1,100 × 60% × 25 min / 60 ≈ 275 hours
Annual labor value       ≈ 275 × 12 × $45 ≈ $148,500

Model cost (measured rate) ≈ 5,000 × $0.000668 × 12 ≈ $40 / year
```

**Net modeled value** ≈ **$148k / year** before ERP integration and change
management. Replace scenario inputs with customer time studies in a pilot; model
cost line is anchored to measured `cost_per_invoice_usd`.

---

## Real client data readiness

| Capability | Status |
|------------|--------|
| Live PDF/image upload + Bedrock extraction | **Shipped** — works with customer documents |
| Tenant policy packs + ERP decision post | **Shipped** — MCP contract; mock ERP in v1 |
| Durable audit trail + HITL in Postgres | **Shipped** |
| Email / SFTP / production ERP | **v2** — [integration roadmap](v2-integration-roadmap.md) |

Pilot-ready: attach real invoices via upload (or v2 ingress), measure
`touchless_rate` and latency on customer mix, compare to APQC/Ardent baselines
using the tables above.

---

## Mapping to UI

| Surface | Behaviour |
|---------|-----------|
| Observability → AP KPIs | `measured` vs `illustrative` badges; cycle-time tile notes benchmark comparison lives in this doc |
| Evals → Manifest gate | Percentages from `evals/results.json` |
| Evals → Upload rows | Per-document scores; summary tiles are **counts** |

---

## Revision history

| Date | Summary |
|------|---------|
| 2026-09-16 | Initial ROI framing — measured vs illustrative split. |
| 2026-09-18 | Reframed as business impact: industry benchmarks + measured latency snapshot + scoped comparison + modeled scenario. Added `make roi-metrics`. |

#### 2026-09-18 — Product positioning pass

- Renamed framing from “illustrative everywhere” to **measured / industry benchmark / modeled**.
- Anchored operational snapshot from `make roi-metrics` (n=2 uploads).
- Added APQC + Ardent comparison tables and touchless-path processing-time math.
