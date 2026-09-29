# Post-build report — building an honest AP exception agent

**Project:** Agentic AP Exception Handling Platform  
**Scope:** Slices 0–14 — specification through eval harness, transparency UI, and portfolio docs  
**Source material:** [`docs/incident-log.md`](incident-log.md) (INC-001–INC-026), ADRs, measured scorecard  

This is a **compiled post-build report** for the whole build — not a single incident
write-up. Individual incidents remain in the incident log; this document is the
narrative a reviewer or interviewer can read in one sitting. See
[Revision history](#revision-history) for how to extend it when the system changes.

---

## Scope note (read this first)

This is a **production-shaped platform** built and hardened on synthetic adversarial data, local/docker deploy, and real Bedrock paths when credentials are exported — with **swappable mock connectors** at ERP/notification edges until v2. It is **pilot-ready on real client PDFs** via upload; full customer volume and ERP cutover are the next deployment step, not a missing core.

Failures below are **real engineering failures** — config validation, persistence gaps, circular eval metrics, misleading error classes — discovered during development, verification, and live upload testing. Business impact is documented in [ROI framing](roi-framing.md) as **measured deployment scores** vs **cited industry benchmarks**, not fabricated pilot ROI.

---

## Summary

We set out to prove that an AP exception agent could combine **model judgment** (extraction, non-PO GL coding) with **deterministic control** (matching, policy, permissions, routing) and **prove quality with numbers that survive scrutiny**. The build succeeded on architecture and CI gates, but repeatedly surfaced a meta-failure mode: **the system could look healthier than it was** — blank UI after reload, extraction errors that were actually expired AWS tokens, eval scorecards that reported 100% task completion while grading against manifest labels, tool correctness pinned to 1.0 without grading real calls.

The through-line of the post-build story is **earning trust in our own instrumentation and UI**, not only in the model. By Slice 13 close:

- `make eval` gates at **100%** policy adherence and routing label consistency (smoke).
- Live Bedrock sample: task **95%**, RAG **100%**, extraction sample **100%**;
  argument correctness **50%** on two agent cases (reported, not gated).
- Run history, source pages, line items, and eval rows persist across API restart.
- Observability KPIs label **measured** vs **benchmark**; [ROI framing](roi-framing.md)
  compares automated latency to APQC/Ardent baselines on a scoped basis.

---

## What broke

Failures clustered into five themes across the build. None was hypothetical.

### 1. Fail-loud configuration (INC-001, INC-002)

Startup rejected valid `EMBEDDING_DIMENSIONS=1024` because Pydantic `Literal` did not coerce strings from `.env`. Separately, blank Langfuse keys with a set host tripped an all-or-nothing observability validator — copying `.env.example` verbatim could not start the app.

**Symptom:** Immediate crash with a precise field name — good for diagnosis, bad for first-run experience until templates and validators were aligned.

### 2. Data and extraction fidelity (INC-003–006, INC-004, INC-019)

Manifest content hashes changed every run until generation was stabilized.Extraction went through a lossy parse → over-corrected prompt → flat confidence arc before two-stage Docling + Bedrock landed. A fixture layout fought the parser; expired AWS credentials surfaced as *“The model did not return a valid extraction”* (INC-019) — auth dressed as model failure.

**Symptom:** Flaky CI dataset, misleading extraction errors, and reviewers blaming the model when the session had expired.

### 3. Policy and RAG correctness (INC-005, INC-007, INC-008)

Three-way match re-litigated totals the price tolerance had already accepted. RAG coding over-grounded an out-of-scope invoice until a deterministic weak-grounding guard was added. Corpus expansion exposed retrieval bugs (hybrid fusion, rail boundaries) that a tiny demo corpus had hidden.

**Symptom:** False FAILs on clean invoices; confident wrong GL proposals with citations that did not support the decision.

### 4. Durability and UI truth (INC-017–020)

The UI read in-memory run registries — reload showed “No runs yet” while KPIs counted real Postgres rows. Source document URLs and extraction line items were volatile across API restart. Demo runs polluted history until the filter required real `field_provenance` on processed documents.

**Symptom:** “It worked in the demo” until the browser refreshed or the API restarted — classic ship-without-durable-state failure.

### 5. Evaluation integrity (INC-021–026)

Early scorecard reported **task_completion 100%** while `route_prediction.py`
peeked at manifest labels (INC-023). Tool and argument correctness were
**hard-coded to 1.0** in places (INC-024). Policy smoke showed **83.3%** until
manifest expectations were aligned with engine behaviour, not papered over
(INC-022 → INC-026). Live eval task prompts over-required PO tools on non-PO
uploads, depressing argument scores (INC-025).

**Symptom:** Green dashboards that would not survive an auditor or a second
engineer reading the grading code.

---

## Why it broke (root causes)

| Theme | Root cause (not just symptom) |
|-------|-------------------------------|
| Config | Strict typing without coercion at the environment boundary; templates that implied half-configured integrations |
| Extraction errors | Error taxonomy collapsed transport/auth and schema validation into one user-facing message |
| Policy/RAG | Rules duplicated across checks; retrieval rail too permissive without a faithfulness floor |
| UI durability | **Two sources of truth** — ephemeral in-process maps vs Postgres audit tables |
| Eval metrics | **Circular or fabricated graders** — labels fed back into predictors; success constants instead of traces |

The deepest systemic issue was **measurement and persistence lagging behind
features**. We built eval UI and scorecards before the graders and storage
guaranteed what they displayed.

---

## How we diagnosed it

1. **Fail-loud validators** — startup and config tests caught INC-001/002 quickly.
2. **Regression tests on fixtures** — dataset manifest verify, policy unit tests,
   extraction eval against `expected_extraction`.
3. **`make eval` / `make eval-live`** — deterministic gate first; live Bedrock
   when AWS available; scorecard written to `evals/results.json`.
4. **Tracing requirements** — Langfuse spans; later, `tool_trace` on real ERP
   calls for DeepEval tool metrics (INC-024).
5. **Dogfooding the UI** — reload, restart API, upload real PDFs, compare Evals
   page to `GET /evals` JSON (INC-020, INC-025).
6. **Incident log discipline** — append on real failure; post-build report drawn
   from log, not invented afterward.

---

## What we changed

### Architecture and product

- **Two-stage extraction** ([ADR-013](adr/ADR-013-two-stage-extraction.md)) with
  provenance for UI highlight-back.
- **PO vs non-PO routing in code** ([ADR-012](adr/ADR-012-po-vs-non-po-routing.md));
  RAG only where GL is not on the PO.
- **Three-layer permissions** ([ADR-015](adr/ADR-015-three-layer-permissions.md));
  no payment execution ([ADR-011](adr/ADR-011-no-payment-execution.md)).
- **Postgres as source of truth** for runs, reviews, checkpoints, eval rows,
  vectors ([ADR-008](adr/ADR-008-pgvector-in-postgres.md)).

### Process and instrumentation

- **`CredentialsError`** at Bedrock seam — expired SSO no longer masquerades as
  bad extraction (INC-019).
- **DB-backed run list and document URLs** — INC-020 surfaces permanent.
- **Eval harness split:**
  - `routing_label_consistency` — deterministic route prediction **without**
    manifest label peek.
  - `task_completion` — live DeepEval on real supervisor runs only.
  - Tool metrics from **`tool_trace`**, not constants.
  - RAG triad uses actual coding question/answer; conservative **min** headline.
- **Per-upload `run_evaluations`** — async scoring after terminal live runs;
  Evals UI polls and labels manifest gate vs upload judges separately.
- **Manifest expectation alignment** — policy smoke 100% after fixing expectations
  vs engine truth (INC-026), not lowering the gate to hide gaps.

### Honesty in the UI

- KPI **measured vs illustrative** badges; cycle-time reduction explicitly not
  a validated savings claim.
- Evals summary: **“In progress”** counts runs being judged — not a quality score.
- Extraction ground-truth badges: matched catalog, foreign catalog, unknown document.

---

## What we would do differently

1. **Define eval contracts before UI** — metric names, graders, and storage
   schema before the Evals page shows percentages.
2. **Deploy earlier** — even docker-only “prod mode” would have forced persistence
   bugs (INC-020) before feature velocity.
3. **CI from day one** — `make eval` in pipeline; GitHub Actions deferred but
   local gate is now real.
4. **Larger live eval sample** — two agent cases swing argument correctness
   wildly (50% vs 75%); report with confidence intervals, not point brags.
5. **Error taxonomy first** — auth, throttle, schema, policy refuse — each gets
   a distinct user-facing class at the boundary.
6. **Real ERP sooner** — mocks hid connector semantics; v2 ADR planned for
   “what broke on contact with a real API.”

---

## Incident index (compiled reference)

| Theme | Incidents |
|-------|-----------|
| Config / startup | INC-001, INC-002 |
| Dataset / manifests | INC-003, INC-006 |
| Extraction | INC-004, INC-019 |
| Policy engine | INC-005 |
| RAG / retrieval | INC-007, INC-008, INC-009 |
| Supervisor / harness | INC-010, INC-012 |
| MCP adapters | INC-011 |
| HITL / checkpoints | INC-013 |
| Observability | INC-014, INC-015, INC-016 |
| Frontend / platform | INC-017, INC-018 |
| Persistence / reload | INC-020 |
| Ops / hardening | INC-021 |
| Eval harness | INC-022, INC-023, INC-024, INC-025, INC-026 |

Full narratives: [`docs/incident-log.md`](incident-log.md).

---

## Measured state at report time

**Deterministic CI (`make eval`, smoke):**

| Metric | Value |
|--------|-------|
| Policy adherence | 100% (30/30) |
| Routing label consistency | 100% (30/30) |

**Live Bedrock sample (`make eval-live --smoke`, 2026-09-16):**

| Metric | Value | Notes |
|--------|-------|-------|
| Task completion | 95% | DeepEval, real supervisor |
| Tool correctness | 100% | From factual `tool_trace` |
| Argument correctness | 50% | 2 agent cases — high variance |
| RAG triad | 100% | Non-PO coding path |
| Extraction accuracy | 100% | 5-case manifest sample |

Artifact: `backend/evals/results.json`.

---

## Related documents

- [ADR-001: System architecture (compiled)](adr/ADR-001-system-architecture.md)
- [ADR index](adr/README.md)
- [Incident log](incident-log.md) — raw append-only record
- [README](../README.md) — quickstart, stack, honest limitations, market context
- [ROI framing](roi-framing.md) · [v2 integration roadmap](v2-integration-roadmap.md)

---

## Revision history

| Date | Summary |
|------|---------|
| 2026-09-16 | Initial post-build report compiled from INC-001–INC-026, ADRs 008–015, and eval scorecard after Slice 13 close. |
| 2026-09-18 | Product positioning pass — pilot-ready scope note; link to benchmark-based ROI framing. |

### Future updates

When the build changes materially (new slice, production deploy, eval regression,
major incident), **append a dated subsection below** and add a row to the table.
Keep the incident log as the canonical detail; summarize here.

#### 2026-09-16 — Initial publication

- Compiled five failure themes across 26 incidents into one narrative.
- Recorded measured smoke + live metrics and explicit PoC scope boundary.
- Linked synthesis ADR and per-topic ADRs for drill-down.

#### 2026-09-18 — Product positioning pass

- Reframed scope as production-shaped / pilot-ready with connector swap path.
- Pointed business impact to [ROI framing](roi-framing.md) (measured vs industry benchmark).

<!-- Template for next entry:
#### YYYY-MM-DD — Short title

- What broke or what improved.
- New INC-### references.
- Updated metric table if scorecard changed.
-->
