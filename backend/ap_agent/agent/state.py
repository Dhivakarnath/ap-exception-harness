"""Supervisor run state — the single object threaded through the graph.

The supervisor is a deterministic `StateGraph` (not an open-ended agent loop):
extraction, policy evaluation, GL coding, and the terminal decision run in a
fixed order, and the one real branch — PO-backed versus non-PO — is decided by
``Invoice.is_non_po``, a pure property, never by a model. So the state here is a
plain typed bag of what each stage produced, not a message list.

Two design rules the LangChain middleware docs are explicit about drive the
shape of this file:

* **Cross-call counters live in state, not on middleware instances.** Budgets
  (model calls, tool calls, tokens) are accumulated here because middleware may
  run concurrently and mutating ``self`` races. The budget middleware reads and
  returns state deltas.
* **State is the audit substrate.** Every field a run produces (the check
  ledger, the coding result, the terminal decision, any error) is captured here
  so persistence and the stream can serialise a run without re-deriving it.

The tool-using sub-agents (extraction/coding) use LangChain's ``AgentState``
(a message list) internally; this ``RunState`` is the *supervisor's* state and
carries the typed artefacts between sub-agent invocations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from ap_agent.core.canonical import Decision, GLCoding, Invoice, Route
from ap_agent.core.checks import CheckLedger
from ap_agent.rag.coding import GLCodingResult


@dataclass(slots=True)
class RunBudget:
    """Hard run budgets, accumulated as the run proceeds (NFR-4).

    Lives in run state rather than on a middleware instance because the docs
    warn that middleware may execute concurrently and mutating instance
    attributes races. The budget middleware reads the limits, compares against
    the running totals, and raises ``BudgetExceededError`` before the offending
    call rather than after — a safety control, never a fallback.
    """

    max_model_calls: int
    max_tool_calls: int
    max_tokens: int
    max_seconds: int

    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    started_at: datetime | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class RunState:
    """Everything one invoice run produces, threaded through the supervisor.

    Deliberately mutable (``slots`` dataclass, not a frozen model): the graph
    fills it stage by stage. It is snapshotted into the frozen `Decision` and
    the persistence rows at the end, so the immutable audit record is derived
    from — not the same object as — this working state.
    """

    # --- identity / inputs ---
    tenant_id: str
    run_id: str
    invoice_id: str
    document_id: str
    trace_id: str | None = None
    """OTEL trace id when deep tracing exported spans for this run."""

    export_deep_traces: bool = False
    """Resolved at trigger time from the global preference and per-run override."""

    policy_version: str = ""

    # --- stage artefacts ---
    invoice: Invoice | None = None
    """Populated by the extraction stage. None until then."""

    is_non_po: bool | None = None
    """The deterministic routing signal, captured once the invoice exists so the
    branch taken is inspectable in the persisted run rather than re-computed."""

    extraction_meta: dict[str, object] | None = None
    """How the invoice was extracted: the Docling parse strategy (structural vs
    OCR, escalation), the Bedrock model + prompt + token/image counts, and
    durations. Populated by the extract node when the run starts from a real
    document; None on the state-first path (a pre-supplied invoice, e.g. the demo
    scenarios) where no parse/model call happened. Persisted and replayed so the
    UI can show the extraction *process*, not just the resulting fields."""

    ledger: CheckLedger = field(default_factory=CheckLedger)
    """The deterministic policy engine's ordered results (empty until policy
    runs)."""

    coding_result: GLCodingResult | None = None
    """The GL-coding stage's proposal + faithfulness verdict, when coding ran
    (non-PO path). None on the PO path, where coding is inherited, not proposed."""

    tool_trace: list[dict[str, object]] = field(default_factory=list)
    """PII-redacted ERP calls executed during this run.

    This is the factual input to live DeepEval tool-selection and
    argument-correctness metrics. It records real calls at the connector seam;
    evals never reconstruct a successful-looking trajectory after the fact.
    """

    gl_coding: GLCoding | None = None
    """The coding actually applied: inherited from the PO on the PO path, or the
    grounded auto-code from `coding_result` on the non-PO path. None when coding
    is routed to a human."""

    decision: Decision | None = None
    """The terminal decision. None until the decision stage runs."""

    # --- accounting / observability ---
    budget: RunBudget | None = None
    context_utilisation: float | None = None
    """Fraction of the token budget the run actually consumed (FR-12.5),
    computed at the end from `budget`."""

    as_of: date | None = None
    """The retrieval as-of date — the invoice date, never wall clock — so a
    back-dated invoice is scored against then-current policy (FR-6.3)."""

    # --- human-in-the-loop ---
    hitl_review_id: str | None = None
    """The `HitlReview` row id when this run escalated to a human (Slice 10).
    None when the run never paused for review."""

    hitl_status: str | None = None
    """The human's decision once resumed: 'approved' / 'edited' / 'rejected'.
    None while pending or when no review happened."""

    pending_hitl: bool = False
    """True when the run is paused at the HITL interrupt awaiting a human
    decision. The caller resumes with `Supervisor.resume(run_id, decision)`."""

    # --- failure ---
    error: dict[str, object] | None = None
    """A serialised `APAgentError.as_dict()` when the run failed loud, for the
    `run_errors` row and the UI error panel. None on a clean run."""

    @property
    def route(self) -> Route | None:
        return self.decision.route if self.decision is not None else None

    def record_context_utilisation(self) -> None:
        """Compute token-budget utilisation from the accumulated budget.

        A touchless PO-backed run should land near zero: it costs no model
        tokens, which is the mechanism that makes the design scale (FR-12.5).
        """
        if self.budget is None or self.budget.max_tokens <= 0:
            self.context_utilisation = None
            return
        self.context_utilisation = self.budget.total_tokens / self.budget.max_tokens
