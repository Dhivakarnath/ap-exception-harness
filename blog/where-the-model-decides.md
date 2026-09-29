# Where the model decides, and where it must not

**Building a replayable AP exception agent with a small model, a deterministic supervisor, and an eval harness that can fail the build.**

An invoice that should have been held can still look like a successful demo. The model reads the PDF, proposes a GL code, says “approve,” and the UI turns green. In accounts payable that is not a product. A controller has to replay *why* the invoice moved, against the policy that was in force that day, with a human as the approver of record when a ceiling is crossed. If the model owns matching, tolerances, or the write, the trail is a conversation, not an audit.

This is a write-up of v1 of an AP exception layer we built to make that trail real. The claim is not that a frontier model is unnecessary in general. The claim is narrower: **on this job, a small multimodal model is enough when control flow is not in the prompt.** Amazon Nova Lite runs extraction and non-PO GL coding. Everything that makes the system employable — routing, three-way match, fourteen policy checks, segregation of duties, the pause for a human, the ERP write contract — is code.

v1 is production-shaped, not production volume. Ingress is upload. ERP and notifications are mock connectors behind a real MCP contract. The quality proof is an adversarial eval gate plus a small live Bedrock sample, labeled as such. The code lives in [`ap-exception-harness`](https://github.com/Dhivakarnath/ap-exception-harness). What follows is the decision record, the measurement, one failure we did not have to publish, and the connector work that comes next.

---

## What “done” means

The system terminates at **approved for payment**. It does not pay. That ceiling is [ADR-011](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/adr/ADR-011-no-payment-execution.md), not a prompt instruction.

That boundary is the product. A payment tool would make a clever agent look more complete and would make every other guarantee harder to defend. The ERP connector may post a bill, place a hold, or raise an exception. There is no `pay`, `disburse`, or `remit` in the toolset; a test asserts the names are disjoint, and the MCP server does not expose a payment scope.

An invoice is done when one of four routes is recorded, with an actor and a timestamp:

| Route | Who may record it |
|-------|-------------------|
| Auto-approve | Policy engine, after every check that can force review has passed and the amount is under the tenant ceiling |
| Route for approval | Agent proposes; a named human becomes the approver of record on resume |
| Hold | Policy or a human reject |
| Reject | Policy (blocked vendor, hard fail) |

Auto-approve from a human is illegal. An agent naming itself as approver on a human-approval route is illegal. Those constraints live in the domain model and again as database check constraints. Defense in depth is not a slogan here; it is the same rule in two layers so a bypass of one still fails.

![Invoice runs with Auto-approved, Held, Needs approval, and Awaiting review](https://raw.githubusercontent.com/Dhivakarnath/ap-exception-harness/main/blog/figures/application_images/Runs-page.png)

*Route and status on recorded runs — not a success rate. Auto-approved, held, and needs-approval are ledger outcomes; awaiting review is a paused graph, not a finished demo. Cost tiles are inference only.*

Industry surveys put average invoice processing at **8.2 days** and all-in cost near **$9.84** ([Ardent Partners, *State of ePayables 2025*](https://www.bottomline.com/cdn/1517/5157/1685/Ardent_Partners_-_State_of_ePayables_2025_-_Bottomline_-_FINAL.pdf)). Exception resolution sits at a **4–5 day** median elapsed ([APQC, cycle time to resolve an invoice error](https://www.apqc.org/resources/benchmarking/open-standards-benchmarking/measures/cycle-time-days-resolve-invoice-error)). Those numbers describe the *problem*. They are not this deployment’s STP rate, and they include mailroom, approver queues, and payment runs we do not run. We compare only the automation step, and we say so next to the number.

---

## Options we refused

The core decision was where the model is allowed to decide. Four shapes were on the table.

| Option | What it optimizes | What it breaks here |
|--------|-------------------|---------------------|
| **A. LLM-as-orchestrator** — one tool loop owns parse quality, PO vs non-PO, matching, GL, and the route | Fast prototype | Control flow cannot be replayed. Tolerances hide in a prompt. An eval “pass” can mean the model agreed with itself. |
| **B. Rules only** — OCR, templates, humans code every non-PO GL | Maximum determinism | Layout variation and non-PO judgment hit a ceiling. The interesting work never gets a model. |
| **C. One path for every invoice** — always three-way match, always RAG | One test matrix | Matching a non-PO invoice is meaningless. RAG on a PO that already has a GL is wasted spend and a conflated eval. |
| **D. Composite** — LangGraph supervisor, policy-as-code, model only on judgment nodes | Auditability with a place for judgment | Two paths to build and explain. Mock ERP still hides real connector quirks. |

We took D. That choice, the options we refused, and the revisit rule are the [compiled architecture decision record](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/adr/ADR-001-system-architecture.md) — written in `docs/` as we built, not for this post.

[Anthropic’s](https://www.anthropic.com/engineering/building-effective-agents) distinction is the right vocabulary: a *workflow* has predefined code paths; an *agent* lets the model direct process and tools. AP matching is not an open-ended task. The step count is known. Latency and cost compound with every extra model call, and a finance stakeholder cannot accept a different tool sequence for the same invoice tomorrow. [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) is in the stack because it lets a graph mix hand-coded steps with LLM-driven ones, persist a paused run, and interrupt for a human — not because “we built an agent.”

The residual risk of D is honest: two paths, three eval surfaces, three permission layers. If v2 has to change the supervisor’s routing or the policy engine’s check names to onboard a customer ERP, the v1 interface was wrong. That rule is in [ADR-001](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/adr/ADR-001-system-architecture.md) and again in the [v2 roadmap](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/v2-integration-roadmap.md) so we cannot quietly violate it later.

---

## The line

**Figure 1.** Who owns the decision. Amber boxes may call Nova Lite. Blue boxes are code. Green boxes are a human or an ERP write.

![Who owns the decision: amber model nodes, blue code path, green human or ERP write](https://raw.githubusercontent.com/Dhivakarnath/ap-exception-harness/main/blog/figures/AP-1.drawio.png)

A PDF arrives through the upload UI. An ingress gateway validates it, content-hash dedupes it, and persists a `documents` row before any model runs. Docling parses structure and escalates to OCR only when the structural yield is implausibly low. Nova Lite then fills a schema-constrained `Invoice` — fail loud on type coercion, with per-field confidence and a source region so the UI can highlight the page.

Routing is a property of that invoice, never a model output. A PO reference takes the PO path: the graph reads purchase order and goods receipt from ERP, runs three-way match, and **inherits** GL from the PO. No PO takes hybrid retrieval (BM25 + pgvector, rerank, mandatory citations) and a second Nova Lite call for a GL proposal. A faithfulness gate can refuse to auto-code. An ungrounded proposal becomes HITL with no account invented to look complete.

Fourteen named checks then run from a versioned YAML pack — manufacturing is PO-heavy, retail is non-PO with a $1,000 touchless ceiling, manufacturing $2,500. Verdicts are pass, fail, flag, or skip. The decide node reads the ledger. It does not re-litigate arithmetic in a prompt.

Writes are earned. Agent RBAC hides write tools from any model that should not see them. Policy middleware refuses `post_erp_action` when the ledger would block it. The MCP server checks scopes before the mock ledger moves. The same forbidden call is tested at the agent layer and again with that layer bypassed. A read-only token’s `post_bill` is refused at the server.

When the route is approval, the graph pauses on a Postgres checkpointer. The run, the invoice, the extraction provenance, and one pending review row are written **before** the interrupt, so a process restart does not erase the queue. A human approves, edits the GL, or rejects — from the run page or the Reviews queue. The human is the actor of record. The model is not.

![Pending review: amount over $1,000 ceiling, Approve / Edit GL / Reject](https://raw.githubusercontent.com/Dhivakarnath/ap-exception-harness/main/blog/figures/application_images/Reviews-page.png)

*HITL as a persisted row: the ceiling names the tier; Approve, Edit GL, and Reject are human actions of record. The queue copy is the restart invariant, not marketing.*

Nova Lite is the worker on two nodes because those nodes are judgment: read a messy page, propose a GL when the PO does not already have one. We did not run a head-to-head against Sonnet or GPT-4o on this invoice set, so this post does not claim the small model “beat” a frontier one. What we can claim is eligibility. Matching, ceilings, and SoD are not in the prompt; a larger model would not make those checks more true.

Industry positioning, labeled as **not this pilot**: on the [FATURA invoice KIE study](https://aws.amazon.com/blogs/machine-learning/document-intelligence-evolved-building-and-evaluating-kie-solutions-that-scale/), Nova Lite text extraction reported F1 **0.9222** at **$0.22 per 1,000 pages** versus Nova Pro **0.9793** F1 at roughly **$2.88–$4.27** per 1,000 pages. The smaller model wins the cost/latency frontier; the larger one wins raw F1. That is a tradeoff table, which is how a model choice should be written. A separate [practitioner bakeoff on a 250-document JSON contract](https://blog.sema.cloud/blog/textract-vs-nova-lite) reported Lite at **$0.00036 / 6.6 s / 242 of 250** against Textract at **$0.01 / 41.9 s / 238 of 250** — and the point of that write-up was that the extraction lane was swappable when Bedrock deprecated the previous model. We took the same lesson: the schema is the contract, the weights are a challenger.

---

## Cost, accuracy, latency — three numbers, three labels

**Figure 2.** How we measure. A gated score may fail CI. A reported score is live and small-n. A cited score is someone else’s survey. They never share a headline.

![Three columns: Gated, Reported, Cited — never one headline metric](https://raw.githubusercontent.com/Dhivakarnath/ap-exception-harness/main/blog/figures/AP-2.drawio.png)

[Hamel Husain’s](https://hamel.dev/blog/posts/evals/) rule is the one we actually used: unsuccessful LLM products fail on evaluation, and [switching the model is not the first lever](https://hamel.dev/blog/posts/evals-faq/) unless error analysis says the model is the problem. [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) is blunter still: when you evaluate “an agent,” you evaluate **harness and model together**. Their CORE-Bench anecdote is the shape of our own incident later — Opus 4.5 moved from 42% to 95% after grader bugs and a less-constrained scaffold were fixed, not after a bigger model.

We split three surfaces on purpose.

### Gated (CI may go red)

`make eval` on a 30-mode adversarial smoke set, 16 September 2026, [`backend/evals/results.json`](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/backend/evals/results.json):

| Metric | Score | Gate |
|--------|-------|------|
| Policy adherence | **100%** (30/30) | ≥ 80% |
| Routing label consistency | **100%** (30/30) | ≥ 70% |

Routing consistency is predicted **without peeking at the manifest label**. That sentence exists because an earlier scorecard did peek, and 100% task completion meant nothing. The full quick dataset is 90 adversarial cases, also 100% policy adherence after we aligned manifest expectations with engine truth rather than lowering the gate. The mix is exception-heavy by design: 15 auto-approve, 42 route-for-approval, 27 hold, 6 reject. It is not a steady-state AP book.

Deterministic tool scoring on live traces grades selection and arguments separately. [DeepEval’s argument judge](https://deepeval.com/docs/metrics-argument-correctness) is kept for task and RAG, where judgment is actually required. Averaging selection with arguments into one “tool use” tile is how a 67% hides a 100% and a 67%. We stopped doing that.

### Reported (not a ship gate)

Live Bedrock sample, `make eval-live --smoke`, same date, **extraction n=5, agent n=2**:

| Metric | Score | Gated? |
|--------|-------|--------|
| Task completion | 95% | No |
| Tool correctness | 100% | No |
| Argument correctness | 50% | No — two cases, volatile |
| RAG triad (min of faithfulness, relevancy, context) | 100% | No |
| Extraction accuracy | 100% (5 cases) | No |

Live tiers need AWS credentials in the shell. They are expensive enough that they are not `make check`. A 50% on two argument cases is a sample-size confession, not a model indictment. [Eugene Yan’s interval arithmetic](https://eugeneyan.com/writing/product-evals/) applies: a point estimate with n=2 does not get a gate. We still show it, because hiding it would recreate the dashboard we had to kill.

Per-upload judges run asynchronously after a terminal live run and land in `run_evaluations`. A paused HITL run is not scored as finished. The Evals UI labels manifest gate versus upload judges so a reader cannot mistake “in progress” for quality.

![Manifest CI gate at 100% beside live Argument correctness at 50%](https://raw.githubusercontent.com/Dhivakarnath/ap-exception-harness/main/blog/figures/application_images/Evals-page.png)

*Same screen, two labels: the manifest CI gate at 100%, live argument correctness at 50% on n=2. The red number is the point — a metric that can look bad is the one worth showing.*

### Operational (this deployment, small n)

`make roi-metrics`, 18 September 2026, document-processed runs with extraction provenance — methodology and the measured-vs-cited split are in the [ROI framing](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/roi-framing.md):

| Metric | Value | Note |
|--------|-------|------|
| Runs in the snapshot | 2 | Volume is still a lab |
| Auto-approve p50 | **10.2 s** | n=1 on that route |
| Hold p50 | **5.4 s** | n=1 |
| Model cost per invoice | **$0.000668** | Inference only |
| Touchless rate | 50% (1 of 2) | Not comparable to [Ardent’s ~35% STP](https://www.bottomline.com/cdn/1517/5157/1685/Ardent_Partners_-_State_of_ePayables_2025_-_Bottomline_-_FINAL.pdf) |

The scoped comparison we *will* make: [APQC’s median **4 days**](https://www.apqc.org/resources/benchmarking/open-standards-benchmarking/measures/cycle-time-days-resolve-invoice-error) elapsed to resolve an invoice error versus **~10 s** on a touchless automation path. That is the decision step, not invoice-to-pay, and HITL still waits on a person. [Ardent’s **$9.84**](https://www.bottomline.com/cdn/1517/5157/1685/Ardent_Partners_-_State_of_ePayables_2025_-_Bottomline_-_FINAL.pdf) all-in must not be netted against **$0.0007** inference without a labor model. Different cost bases in one subtraction is how a blog becomes a pitch.

Latency for a deterministic graph is a forecastable p95 once volume exists. An open tool loop’s step count is a random variable. We chose the graph in part so a latency budget is a property of the pipeline, not a hope about the model.

---

## The dashboard that could lie

The failures that taught us the most were not “the model hallucinated a total.” They were **green numbers that would not survive a second engineer reading the grader**. The compiled write-up is the [post-build report](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/post-build-report.md); the raw trail is the [incident log](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/incident-log.md).

Early scorecard reported task completion at 100% while the route predictor read the manifest label it was supposed to be judged against. Tool and argument correctness were hard-coded to 1.0 in places, so DeepEval never saw a real ERP trace. Policy smoke sat at 83% until we fixed the *expectations*, not the engine, to match what the checks actually do. Live task prompts over-required PO tools on non-PO uploads and punished argument scores for calls the rubric should not have demanded.

That is the same family of bug [Anthropic documents on CORE-Bench](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents): the agent was not 42% capable; the grader was wrong and the scaffold was too tight. [Hamel’s Lucy](https://hamel.dev/blog/posts/evals/) plateaued on vibe-checks. Our plateau was a CI that could not fail.

The invariant we added: **grade outcomes and traces, not the sheet you already know.** Routing labels are predicted from the invoice and the ledger. Tool scores come from `tool_trace` recorded at the ERP seam. Policy smoke is a bit-for-bit ledger comparison. Headline RAG is a **minimum** of the triad, never an average that hides a weak dimension. If a metric cannot go red, it does not belong on the scorecard.

Two cousins, same lesson. Expired AWS credentials surfaced as “the model did not return a valid extraction” until we split `CredentialsError` at the Bedrock seam. The UI listed runs from process memory, so a refresh showed “No runs yet” while Postgres already had the invoices — and a paused HITL run skipped persistence entirely, so a restart wiped the review queue. Two sources of truth is how a demo lies after lunch. The run, the document image, the field provenance, and the pending review now have to exist as rows before we tell a human the case is waiting.

We would still do three things earlier: define eval contracts before the Evals page, deploy a docker “prod mode” soon enough to catch the empty-queue bug, and put `make eval` in hosted CI on day one. The local gate is real; GitHub Actions is still a v2 item.

---

## What v1 will not pretend to be

Upload is the only live ingress. Email, ERP webhook, and bucket/SFTP adapters exist as protocol-conforming stubs that raise rather than silently drop a document. That is deliberate: a stub that swallows is worse than an honest `NotImplementedError`.

ERP is a QuickBooks Online–shaped mock. The MCP tool names are the contract: `get_purchase_order`, `get_goods_receipt`, `list_historical_bills`, `post_erp_action` (approve / hold / flag). Canonical-to-ERP field mapping lives in the connector, not in the supervisor. Notifications are a console channel with the same `notify_approver` shape a Slack app would keep.

The adversarial dataset is synthetic diversity. It does not close the gap to a customer’s real book. Slice 15 — full E2E on a clean deploy, latency budgets, HITL on both channels, induced failure with no fallback — is still a checklist, not a trophy.

None of that is an apology for mocks. It is the fidelity label Fin and Anthropic put next to a number: this score is a frozen replay; that score is production. Mixing them in one table without labels is the amateur tell.

---

## What would change our mind

v2 is connector swaps, not a rewrite. The interface list is the [v2 integration roadmap](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/v2-integration-roadmap.md).

Email, webhook, and bucket adapters should hit the same `InvoiceReceived` event the upload path already hits. A customer ERP — QuickBooks Online or ERPNext — should implement the same MCP tools, with OAuth, idempotent posts, and sandbox tests that record what the mock hid: partial PO lines, rate limits, stale GRNs, multi-currency quirks. Slack interactivity should resume the same HITL endpoint the in-app queue uses. If any of that requires editing `supervisor.py` routing or `policy/engine.py` checks, we fix the v1 interface first.

We would revisit Nova Lite on extraction if a held-out customer sample showed field F1 that a larger model recovered and the harness did not — after error analysis, not before. We would gate argument correctness only when n is large enough that a 50% is not a coin flip. We would publish p95 latency once `make roi-metrics` is no longer a two-row snapshot.

The test for this architecture is simple. Give it an invoice that looks auto-approvable and is not — missing GRN, duplicate, blocked vendor, over ceiling. The model may still read the page well. The ledger has to refuse. The review has to survive a restart. The scorecard has to be able to go red.

If those hold with a small model, the harness was the product. If they only hold after we put matching back in a prompt, we chose the wrong option in the table above, and we should say so.

---

## Sources

Industry and craft citations are linked at first mention in the body.

The records below are not appendices written for this essay. They live in [`docs/`](https://github.com/Dhivakarnath/ap-exception-harness/tree/main/docs), next to the code, because they were the working papers of the build. Each link is the file on GitHub.

| Record | What it is |
|--------|------------|
| [This essay on GitHub](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/blog/where-the-model-decides.md) | Canonical post, with figures |
| [ADR-001 — system architecture](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/adr/ADR-001-system-architecture.md) | Options, the composite decision, consequences, revisit rule |
| [ADR-011 — no payment execution](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/adr/ADR-011-no-payment-execution.md) | Terminates at approved for payment |
| [ADR index](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/adr/README.md) | Routing, extraction, eval scoring, three-layer permissions |
| [Post-build report](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/post-build-report.md) | Failures compiled from the incident log |
| [Incident log](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/incident-log.md) | Append-only trail (INC-001–026) |
| [ROI framing](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/roi-framing.md) | Measured vs cited vs modeled — how not to mix them |
| [v2 integration roadmap](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/docs/v2-integration-roadmap.md) | Connector swaps; if the supervisor must change, v1 was wrong |
| [`backend/evals/results.json`](https://github.com/Dhivakarnath/ap-exception-harness/blob/main/backend/evals/results.json) | The gated scorecard this post quotes |
