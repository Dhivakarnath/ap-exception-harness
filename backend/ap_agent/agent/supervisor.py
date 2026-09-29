"""The supervisor graph — the deterministic backbone that composes the pipeline.

The whole system's control flow lives here, and it is deliberately a
`StateGraph` of plain Python nodes rather than one open-ended tool-calling agent.
The reason is the project's central claim: an AP decision an audit trail must be
able to replay byte-for-byte cannot have its *control flow* decided by a model.
So the backbone is code — extraction, then a deterministic route on
``Invoice.is_non_po``, then the policy engine, then (on the non-PO branch) GL
coding, then a terminal decision — and the model is called only inside the two
nodes where genuine reading/judgement is needed (extraction and coding), each
behind the middleware harness.

The nodes and edges::

    START
      -> extract            (model: read the document into a canonical Invoice)
      -> route              (pure: is_non_po? — the one real branch)
           |-- non-PO -->  code_gl   (model: propose a grounded GL code)
           |-- PO-backed --------------\\
                                        v
      -> policy             (pure: run all 14 deterministic checks)
      -> decide             (pure: ledger + gate + coding -> Route + Decision)
      -> END

Every node is a function ``(RunState) -> RunState`` (mutating in place, then
returning it): the state is the single object threaded through, and the terminal
`Decision` and the persisted rows are derived from it. Failures propagate as
typed `APAgentError`s; a node does not catch-and-continue, because a partial run
that looked complete is the failure mode this system exists to prevent.

**The gate runs twice, on purpose.** The policy node computes the ledger and the
`GateDecision` deterministically (this is what drives the route). The
`post_erp_action` tool is *additionally* wrapped by the policy-engine middleware,
so even if a future caller invoked that tool outside this graph, the write would
still be gated. Defence in depth: the decision here is the reasoning, the
middleware there is the enforcement, and Slice 9's MCP layer is the third.
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import fields as dataclasses_fields
from datetime import UTC, date, datetime
from functools import wraps
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from ap_agent.agent.context_slicing import coding_slice
from ap_agent.agent.erp_client import ErpClient, TracedErpClient
from ap_agent.agent.policy_gate import GateVerdict, PolicyGateContext, evaluate_gate
from ap_agent.agent.state import RunBudget, RunState
from ap_agent.core.canonical import Actor, Decision, GLCoding, Invoice, Route
from ap_agent.core.policy_pack import PolicyPack
from ap_agent.errors import (
    APAgentError,
    BudgetExceededError,
    ErrorContext,
    ExtractionError,
    HumanInputRequiredError,
)
from ap_agent.policy.context import HistoricalBill
from ap_agent.policy.engine import evaluate_all
from ap_agent.rag.coding import GLCodingResult, propose_gl_coding
from ap_agent.rag.rail import apply_rail

if TYPE_CHECKING:
    from ap_agent.agent.hitl import ReviewCard, ReviewDecision
    from ap_agent.extract.extractor import InvoiceExtractor
    from ap_agent.rag.coding import CodingModel
    from ap_agent.rag.rerank import Reranker
    from ap_agent.rag.retriever import HybridRetriever

# The agent is the coder and router, never the approver of record (FR-4.11).
AGENT_ACTOR = Actor.AGENT
POLICY_ACTOR = Actor.POLICY_ENGINE


@dataclass(slots=True)
class SupervisorDeps:
    """Everything the supervisor's nodes call into, injected at build time.

    Injected rather than imported as singletons so a test drives the whole graph
    with scripted models, an in-memory ERP, and a fake retriever — no network, no
    Bedrock, no monkeypatching. The same object type carries the live
    dependencies in the demo and in production.
    """

    tenant_id: str
    policy: PolicyPack
    erp: ErpClient
    retriever: HybridRetriever
    coding_model: CodingModel
    session_factory: object
    """Zero-arg callable returning a context-managed Session (``session_scope``)."""

    reranker: Reranker | None = None
    """Optional relevance reranker. Live runs use the local cross-encoder;
    tests use deterministic RRF ordering truncated to the configured top-k."""

    # Extraction is optional in state-first runs where the invoice is supplied
    # already parsed (tests, replays). When present, the extract node uses it.
    extractor: InvoiceExtractor | None = None
    parsed_document: object | None = None
    """A pre-parsed document for the extract node, when the run starts from a
    document rather than an already-extracted invoice."""

    page_images: list[tuple[str, bytes]] | None = None
    """Rendered page images for the document-driven path, forwarded to
    `InvoiceExtractor.extract` so a scanned or picture-bearing invoice gets
    multimodal grounding, not text alone. `None`/empty is fine: the extractor
    only attaches images when `should_attach_images` says the parse needs them."""

    document_media_type: str = "application/pdf"

    emitter: object | None = None
    """Optional `EventEmitter` (observability). When present, the supervisor
    emits a normalized `RunEvent` per stage, per check, for the decision, and for
    cost — the same envelope the UI consumes live and on replay. None disables
    emission (the run behaves identically; it just is not observed)."""

    checkpointer: object | None = None
    """Optional LangGraph checkpointer. Required only for interrupt/resume
    (HITL). The deterministic backbone runs to completion without one; Slice 10
    supplies a durable checkpointer and wires resume to `HitlReview.thread_id`.
    An `InMemorySaver` is enough for the in-process demo and tests."""

    langchain_callbacks: list[Any] | None = None
    """Optional DeepEval `CallbackHandler` list for span-level eval (Slice 13)."""


class Supervisor:
    """Builds and runs the deterministic supervisor graph for one tenant.

    Construct once per (tenant, dependency set); ``run`` executes one invoice.
    The compiled graph is cached so repeated runs reuse it.
    """

    def __init__(self, deps: SupervisorDeps) -> None:
        self._deps = deps
        # Wrap the ERP client so every ERP call (read or write) is an OTEL tool
        # span with PII-redacted args, nested under the node span. Kept on `self`
        # rather than mutating the shared `deps` (which a caller may reuse for a
        # second Supervisor). The proxy satisfies the same `ErpClient` Protocol
        # and is a transparent no-op when tracing is off, so it works identically
        # for the in-process and MCP clients and changes nothing about behaviour.
        self._erp = TracedErpClient(deps.erp)
        self._graph = self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> object:
        graph: StateGraph[RunState, None, RunState, RunState] = StateGraph(RunState)
        # Each node opens its own OTEL span (see `_node_span` used at the top of
        # every node body), so a reviewer sees the real pipeline shape in the
        # trace (run > node > check), not a flat run. The span nests under the
        # run span automatically via the OTEL context.
        # The `# type: ignore[call-overload]` mirrors the compile() ignore
        # below: a span-wrapped node is a `Callable[[RunState], RunState]`, which
        # LangGraph's overloaded `add_node` stubs do not infer as cleanly as a
        # bound method, though it is exactly the accepted node shape at runtime.
        graph.add_node("extract", self._traced_node("extract", self._extract_node))  # type: ignore[call-overload]
        graph.add_node("code_gl", self._traced_node("code_gl", self._code_gl_node))  # type: ignore[call-overload]
        graph.add_node("policy", self._traced_node("policy", self._policy_node))  # type: ignore[call-overload]
        graph.add_node("decide", self._traced_node("decide", self._decide_node))  # type: ignore[call-overload]
        graph.add_node("hitl", self._traced_node("hitl", self._hitl_node))  # type: ignore[call-overload]

        graph.add_edge(START, "extract")
        # The one real branch, decided by a pure property — never by a model.
        graph.add_conditional_edges(
            "extract",
            self._route_after_extract,
            {"non_po": "code_gl", "po_backed": "policy"},
        )
        graph.add_edge("code_gl", "policy")
        graph.add_edge("policy", "decide")
        # A ROUTE_FOR_APPROVAL decision detours through the HITL node (pause,
        # notify, wait for the human, resume); every other route terminates. The
        # branch is a pure read of the decision the decide node already made.
        graph.add_conditional_edges(
            "decide",
            self._route_after_decide,
            {"hitl": "hitl", "done": END},
        )
        graph.add_edge("hitl", END)
        # A checkpointer is only needed for interrupt/resume (HITL). When one is
        # supplied the compiled graph persists state between steps so a paused
        # run can resume; without one the deterministic backbone runs straight
        # through. Passing checkpointer=None to compile() is the no-op default.
        if self._deps.checkpointer is not None:
            return graph.compile(checkpointer=self._deps.checkpointer)  # type: ignore[arg-type]
        return graph.compile()

    # ------------------------------------------------------------------- run
    def run(self, state: RunState) -> RunState:
        """Execute the pipeline over one invoice run state.

        Accepts a `RunState` that already carries either a parsed document (the
        extract node will read it) or an `invoice` (tests/replays skip
        extraction). Returns the same state, now carrying the terminal decision —
        or an `error` dict if the run failed loud.
        """
        self._erp.reset_calls()
        if state.budget is None:
            state.budget = _default_budget()
        state.budget.started_at = datetime.now(UTC)
        state.policy_version = self._deps.policy.version
        config = self._invoke_config(state.run_id)
        # Register the emitter so the SSE bridge can stream this run live; also
        # emit a run-started stage event and open the OTEL run span.
        self._register_emitter()
        from ap_agent.llm.invoke_context import langchain_callbacks

        from ap_agent.observability.tracing import active_deep_tracing

        try:
            with (
                active_deep_tracing(state.export_deep_traces),
                self._run_span(state),
                langchain_callbacks(self._deps.langchain_callbacks),
            ):
                result = self._graph.invoke(state, config=config)  # type: ignore[attr-defined]
        except APAgentError as exc:
            state.error = exc.as_dict()
            self._emit_error(exc)
            state.tool_trace.extend(self._erp.drain_calls())
            state.record_context_utilisation()
            return state
        finally:
            self._unregister_emitter(state.run_id)
            from ap_agent.observability.tracing import force_flush

            force_flush()
        return self._finalise(result, state)

    def _register_emitter(self) -> None:
        if self._deps.emitter is None:
            return
        from ap_agent.observability.sse import register_emitter

        register_emitter(self._deps.emitter)  # type: ignore[arg-type]

    def _unregister_emitter(self, run_id: str) -> None:
        if self._deps.emitter is None:
            return
        from ap_agent.observability.sse import unregister_emitter

        unregister_emitter(run_id)

    def _run_span(self, state: RunState) -> Any:
        from ap_agent.observability.tracing import run_span_capture

        return run_span_capture(
            run_id=state.run_id,
            tenant_id=state.tenant_id,
            invoice_id=state.invoice_id,
            on_trace_id=lambda tid: setattr(state, "trace_id", tid),
        )

    def _traced_node(
        self, name: str, fn: Callable[[RunState], RunState]
    ) -> Callable[[RunState], RunState]:
        """Wrap a graph node so its execution is an OTEL span *and* a stage event.

        The span opens when the graph runs the node, nesting under the run span
        (the run > node > check tree a reviewer expects). Around it we also emit a
        ``stage`` RunEvent on entry (``running``) and exit (``ok`` with the
        elapsed ms), so the UI's pipeline inspector shows the run advancing node
        by node in real time — not just the final verdict. When tracing is off
        the span is a no-op; when no emitter is attached the stage emit is
        skipped; either way the node's behaviour and cost are unchanged.
        """
        from ap_agent.observability.tracing import node_span

        @wraps(fn)
        def _wrapped(state: RunState) -> RunState:
            emitter = self._deps.emitter
            started = time.perf_counter()
            if emitter is not None:
                emitter.stage(name=name, status="running")  # type: ignore[attr-defined]
            with node_span(name):
                result = fn(state)
            if emitter is not None:
                emitter.stage(  # type: ignore[attr-defined]
                    name=name,
                    status="ok",
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
            return result

        return _wrapped

    def _model_span(self, *, purpose: str) -> Any:
        """A span around a real model call (extraction / GL coding).

        Nests under the current node span, and carries the configured Bedrock
        model id plus ``gen_ai.*`` attributes so an LLM-native backend renders it
        as a generation. A no-op when tracing is off.
        """
        from ap_agent.config import get_settings
        from ap_agent.observability.tracing import model_span

        return model_span(model_id=get_settings().bedrock_model_id, purpose=purpose)

    @staticmethod
    def _record_model_usage(span: Any, input_tokens: int, output_tokens: int) -> None:
        """Stamp a model call's token usage onto its span once the call returns.

        Token counts are only known post-response, so they are set on the open
        span here. A no-op when the span is None (tracing off)."""
        from ap_agent.observability.tracing import set_model_usage

        set_model_usage(span, input_tokens=input_tokens, output_tokens=output_tokens)

    def _emit_error(self, exc: APAgentError) -> None:
        if self._deps.emitter is not None:
            self._deps.emitter.error(exc.as_dict())  # type: ignore[attr-defined]

    def _emit_extraction(self, invoice: Invoice, *, meta: dict[str, object] | None = None) -> None:
        """Emit the extracted header fields with confidence + source region.

        Feeds the UI's ExtractionPanel and the document highlight-back: each field
        carries the ``element_ref`` of the region it came from, so a click can
        point at the exact spot on the invoice (FR-2.5). Only the scalar header
        fields are surfaced (line items have their own table view); a field with
        no recovered region simply omits ``region_ref``. ``meta`` carries the
        extraction *process* (Docling strategy, Bedrock model + tokens), so the
        UI can show how — not just what — was read.
        """
        emitter = self._deps.emitter
        if emitter is None:
            return

        def field(name: str, extracted: Any) -> dict[str, Any] | None:
            if extracted is None:
                return None
            region = getattr(extracted, "region", None)
            value = getattr(extracted, "value", None)
            bbox = getattr(region, "bbox", None)
            bbox_payload = (
                {
                    "left": bbox.left,
                    "top": bbox.top,
                    "right": bbox.right,
                    "bottom": bbox.bottom,
                }
                if bbox is not None
                else None
            )
            return {
                "name": name,
                "value": None if value is None else str(value),
                "confidence": getattr(extracted, "confidence", None),
                "region_ref": getattr(region, "element_ref", None),
                "page": getattr(region, "page", None),
                "bbox": bbox_payload,
            }

        candidates = [
            field("invoice_number", invoice.invoice_number),
            field("invoice_date", invoice.invoice_date),
            field("vendor_name", invoice.vendor_name),
            field("currency", invoice.currency),
            field("subtotal", invoice.subtotal),
            field("total_amount", invoice.total_amount),
            field("po_reference", invoice.po_reference),
        ]
        fields = [f for f in candidates if f is not None]
        line_items = _line_items_payload(invoice)
        emitter.extraction(  # type: ignore[attr-defined]
            fields=fields, line_items=line_items, meta=meta
        )

    def resume(self, run_id: str, decision: object) -> RunState:
        """Resume a run paused at the HITL interrupt with the human's decision.

        ``decision`` is a `ReviewDecision` (or an equivalent dict). Requires the
        same checkpointer and ``run_id`` (thread id) the paused run used, so the
        graph continues exactly where ``interrupt()`` stopped — the resume value
        becomes the return of that call. Returns the completed run state.
        """
        from langgraph.types import Command

        if self._deps.checkpointer is None:
            raise HumanInputRequiredError(
                "Cannot resume a HITL run without a checkpointer; the paused "
                "state was never persisted.",
                context=ErrorContext(stage="supervisor.resume"),
            )
        config = self._invoke_config(run_id)
        from ap_agent.llm.invoke_context import langchain_callbacks

        try:
            with langchain_callbacks(self._deps.langchain_callbacks):
                result = self._graph.invoke(  # type: ignore[attr-defined]
                    Command(resume=decision), config=config
                )
        except APAgentError as exc:
            fallback = RunState(
                tenant_id=self._deps.tenant_id, run_id=run_id, invoice_id="", document_id=""
            )
            fallback.error = exc.as_dict()
            return fallback
        return self._finalise(
            result,
            RunState(tenant_id=self._deps.tenant_id, run_id=run_id, invoice_id="", document_id=""),
        )

    def is_paused(self, result: RunState) -> bool:
        """Whether a `run()` result is paused at the HITL interrupt.

        True when the run reached the HITL node and is awaiting a human decision
        (no terminal human decision recorded yet). The caller resumes with
        `resume(run_id, decision)`.
        """
        return result.pending_hitl

    def _thread_config(self, run_id: str) -> dict[str, object] | None:
        """Thread config for the checkpointer, keyed on the run id.

        A checkpointed graph requires a ``thread_id`` so a paused run can be
        found and resumed; without a checkpointer there is nothing to thread.
        """
        if self._deps.checkpointer is None:
            return None
        return {"configurable": {"thread_id": run_id}}

    def _invoke_config(self, run_id: str) -> dict[str, object] | None:
        """LangGraph invoke config: checkpointer thread + optional callbacks."""
        config: dict[str, object] = {}
        thread = self._thread_config(run_id)
        if thread:
            config.update(thread)
        if self._deps.langchain_callbacks:
            config["callbacks"] = self._deps.langchain_callbacks
        return config or None

    def _finalise(self, result: object, state: RunState) -> RunState:
        """Turn a graph result into a typed `RunState`, detecting an interrupt.

        LangGraph returns the final channel-value dict on completion, or a dict
        carrying ``__interrupt__`` when the run paused at ``interrupt()``. A
        paused run is marked ``pending_hitl`` so the caller knows to resume.
        """
        if isinstance(result, dict) and result.get("__interrupt__"):
            paused = _run_state_from_channels(result)
            _rehydrate_state(paused)
            paused.tool_trace.extend(self._erp.drain_calls())
            paused.pending_hitl = True
            paused.record_context_utilisation()
            return paused
        final = _run_state_from_channels(result) if isinstance(result, dict) else state
        _rehydrate_state(final)
        final.tool_trace.extend(self._erp.drain_calls())
        final.record_context_utilisation()
        return final

    # --------------------------------------------------------- budget guard
    def _check_budget(self, state: RunState, *, stage: str) -> None:
        """Enforce the hard run budgets on the deterministic backbone (NFR-4).

        The ``BudgetMiddleware`` bounds a *sub-agent's* model/tool calls, but the
        supervisor's own nodes run outside that middleware, so the budget is
        re-checked here at every node entry. A breach raises
        ``BudgetExceededError`` — halt and escalate, never a silent truncation.
        Wall-clock and token totals are the meaningful bounds on this backbone;
        the per-node entry also caps runaway step counts implicitly (the graph is
        acyclic, so nodes are bounded, but the check is cheap and explicit).
        """
        budget = state.budget
        if budget is None:  # pragma: no cover - run() always sets one
            return
        if budget.started_at is not None:
            elapsed = (datetime.now(UTC) - budget.started_at).total_seconds()
            if elapsed > budget.max_seconds:
                raise BudgetExceededError(
                    "wall_clock_seconds",
                    budget.max_seconds,
                    round(elapsed, 1),
                    context=ErrorContext(stage=f"supervisor.{stage}", tenant_id=state.tenant_id),
                )
        if budget.model_calls > budget.max_model_calls:
            raise BudgetExceededError(
                "model_calls",
                budget.max_model_calls,
                budget.model_calls,
                context=ErrorContext(stage=f"supervisor.{stage}", tenant_id=state.tenant_id),
            )
        if budget.total_tokens > budget.max_tokens:
            raise BudgetExceededError(
                "tokens",
                budget.max_tokens,
                budget.total_tokens,
                context=ErrorContext(stage=f"supervisor.{stage}", tenant_id=state.tenant_id),
            )

    # ----------------------------------------------------------------- nodes
    def _extract_node(self, state: RunState) -> RunState:
        """Read the document into a canonical `Invoice` (model step).

        Skipped when the state already carries an invoice (a replay or a test
        that supplies one directly). Extraction failure is FATAL and propagates:
        a coerced or partial reading is exactly the silent wrong-payment risk the
        project forbids.
        """
        self._check_budget(state, stage="extract")
        if state.invoice is not None:
            state.is_non_po = state.invoice.is_non_po
            return state

        if self._deps.extractor is None or self._deps.parsed_document is None:
            raise ExtractionError(
                "The extract node needs either an already-extracted invoice on "
                "the run state, or an extractor plus a parsed document. Neither "
                "was supplied.",
                context=ErrorContext(
                    stage="supervisor.extract",
                    tenant_id=state.tenant_id,
                    document_id=state.document_id,
                ),
            )
        with self._model_span(purpose="extraction") as span:
            outcome = self._deps.extractor.extract(
                self._deps.parsed_document,  # type: ignore[arg-type]
                tenant_id=state.tenant_id,
                document_id=state.document_id,
                invoice_id=state.invoice_id,
                page_images=self._deps.page_images,
            )
            self._record_model_usage(span, outcome.input_tokens, outcome.output_tokens)
        state.invoice = outcome.invoice
        state.is_non_po = outcome.invoice.is_non_po
        state.as_of = outcome.invoice.invoice_date.value
        state.extraction_meta = self._extraction_meta(outcome)
        if state.budget is not None:
            state.budget.input_tokens += outcome.input_tokens
            state.budget.output_tokens += outcome.output_tokens
            state.budget.model_calls += 1
        self._emit_extraction(state.invoice, meta=state.extraction_meta)
        return state

    def _extraction_meta(self, outcome: Any) -> dict[str, object]:
        """Distil how this invoice was extracted, for the transparency UI.

        Captures the two real stages a reviewer wants to see: the Docling parse
        (structural text layer vs OCR, and whether it escalated) and the Bedrock
        extraction (which model, whether page images were attached to the vision
        call, prompt version, schema-repair attempts, tokens). This is discarded
        after the node otherwise; persisting it is what lets a completed run
        explain its own extraction on reload.
        """
        parsed = self._deps.parsed_document
        strategy = getattr(outcome, "parse_strategy", None)
        return {
            "parse_strategy": getattr(strategy, "value", str(strategy) if strategy else None),
            "escalated_to_ocr": bool(getattr(outcome, "escalated_to_ocr", False)),
            "parser_name": getattr(parsed, "parser_name", None),
            "parser_version": getattr(parsed, "parser_version", None),
            "page_count": getattr(parsed, "page_count", None),
            "text_length": getattr(parsed, "text_length", None),
            "parse_duration_ms": getattr(parsed, "duration_ms", None),
            "model_id": getattr(outcome, "model_id", None),
            "prompt_version": getattr(outcome, "prompt_version", None),
            "images_attached": int(getattr(outcome, "images_attached", 0)),
            "attempts_used": int(getattr(outcome, "attempts_used", 1)),
            "input_tokens": int(getattr(outcome, "input_tokens", 0)),
            "output_tokens": int(getattr(outcome, "output_tokens", 0)),
            "extract_duration_ms": getattr(outcome, "duration_ms", None),
        }

    def _route_after_extract(self, state: RunState) -> str:
        """The deterministic PO vs non-PO branch (FR-5.1).

        A pure read of `Invoice.is_non_po`. This is the routing decision the
        whole design insists a model must never make: matching applies to a
        PO-backed invoice and cannot to a non-PO one, and that is a fact about
        the document, not a judgement call.
        """
        if state.invoice is None:  # pragma: no cover - guarded by extract node
            raise ExtractionError(
                "Routing reached with no invoice on state.",
                context=ErrorContext(stage="supervisor.route", tenant_id=state.tenant_id),
            )
        return "non_po" if state.invoice.is_non_po else "po_backed"

    def _code_gl_node(self, state: RunState) -> RunState:
        """Propose a grounded GL code for a non-PO invoice (model step).

        Runs the RAG pipeline — retrieve, rail, propose + faithfulness gate — and
        records the result. It does not *decide* the route; the decide node reads
        `coding_result.outcome` to translate an ungrounded/low-confidence code
        into a human route. Coding never fabricates: an ungrounded proposal is
        dropped by the gate upstream of here.
        """
        self._check_budget(state, stage="code_gl")
        invoice = state.invoice
        if invoice is None:  # pragma: no cover - guarded by route
            return state

        as_of = state.as_of or invoice.invoice_date.value
        # The coding step sees only its slice: a tight invoice summary + the
        # retrieved clauses. Never the policy pack, the ERP records, or the
        # ledger — that isolation is what keeps the code grounded in the clauses
        # (FR-15.5). `coding_slice` is the single statement of that boundary.
        query = coding_slice(invoice, []).invoice_summary
        session_scope = self._deps.session_factory
        with session_scope() as session:  # type: ignore[operator]
            hits = self._deps.retriever.retrieve(
                session, tenant_id=state.tenant_id, query=query, as_of=as_of
            )
        from ap_agent.config import get_settings
        from ap_agent.rag.rerank import NoopReranker

        reranker = self._deps.reranker or NoopReranker()
        hits = reranker.rerank(
            query,
            hits,
            top_k=get_settings().coding_retrieval_top_k,
        )
        railed = apply_rail(hits)
        with self._model_span(purpose="gl_coding") as span:
            result: GLCodingResult = propose_gl_coding(
                self._deps.coding_model,
                invoice_summary=query,
                rail_result=railed,
                min_confidence=self._deps.policy.thresholds.min_confidence_for_gl_coding,
            )
            self._record_model_usage(span, result.input_tokens, result.output_tokens)
        state.coding_result = result
        if result.auto_applicable:
            state.gl_coding = result.coding
        # A model call happened; account its real token usage so the run's cost
        # reflects the coding step (scripted stubs report zero). The rail-empty
        # path never called the model, so usage is legitimately zero there.
        if state.budget is not None:
            state.budget.input_tokens += result.input_tokens
            state.budget.output_tokens += result.output_tokens
            state.budget.model_calls += 1
        return state

    def _policy_node(self, state: RunState) -> RunState:
        """Run all deterministic checks over the fully-loaded context (pure).

        Loads the PO/GRN/vendor/history from the ERP (the read side of the
        connector), assembles the `PolicyEvaluationContext`, and runs
        `evaluate_all`. On the PO path this includes the three-way match; on the
        non-PO path matching SKIPs and the coding result informs the decision.
        """
        self._check_budget(state, stage="policy")
        invoice = state.invoice
        if invoice is None:  # pragma: no cover - guarded upstream
            return state

        gate_ctx = self._load_gate_context(invoice)
        state.ledger = evaluate_all(gate_ctx.to_evaluation_context())
        # Emit exactly one observability event per check, in evaluation order,
        # with an OTEL span per check carrying its threshold/actual attributes.
        emitter = self._deps.emitter
        for result in state.ledger.results:
            with node_span_check(result):
                if emitter is not None:
                    emitter.check(result.as_stream_payload())  # type: ignore[attr-defined]
        return state

    def _decide_node(self, state: RunState) -> RunState:
        """Translate ledger + gate + coding into a terminal `Route` + `Decision`.

        The single place a route is chosen, and it is a pure function of
        deterministic inputs:

        * a gate **reject** -> ``HOLD`` (a hard failure a human must resolve),
        * a gate **require_approval** -> ``ROUTE_FOR_APPROVAL`` at the DOA tier,
        * a non-PO invoice whose coding did not auto-apply -> ``ROUTE_FOR_APPROVAL``
          (uncited/low-confidence coding always sees a human, FR-5.4),
        * otherwise -> ``AUTO_APPROVE`` (clean, within ceiling, coding grounded
          or inherited).

        The agent records itself as coder/router but never as approver of record;
        the `Decision` model re-validates that segregation of duties invariant.
        """
        invoice = state.invoice
        if invoice is None:  # pragma: no cover
            return state

        # Re-load the ERP counterparts here rather than stashing them on the
        # instance from the policy node: the load is deterministic and cheap
        # (in-process reads), and mutating instance state across nodes races
        # under LangGraph's execution model. The ledger itself is on the state.
        gate_ctx = self._load_gate_context(invoice)
        gate = evaluate_gate(invoice=invoice, policy=self._deps.policy, ledger=state.ledger)

        # Determine the GL coding to record: inherited on the PO path, grounded
        # auto-code on the non-PO path, or none (route to human) otherwise.
        gl_coding = self._resolve_gl_coding(state, gate_ctx)
        coding_forces_human = bool(state.is_non_po) and (
            state.coding_result is None or not state.coding_result.auto_applicable
        )

        route, decided_by, tier, rationale = self._choose_route(
            gate_verdict=gate.verdict,
            gate_reason=gate.reason,
            gate_tier=gate.required_tier,
            coding_forces_human=coding_forces_human,
            coding_result=state.coding_result,
        )

        citations = gl_coding.citations if gl_coding is not None else ()
        state.gl_coding = gl_coding
        state.decision = Decision(
            invoice_id=invoice.invoice_id,
            route=route,
            decided_by=decided_by,
            rationale=rationale,
            decided_at=datetime.now(UTC),
            gl_coding=gl_coding,
            required_approver_tier=tier,
            approver_identity=None,  # never the agent (SoD); a human fills this on approval
            citations=citations,
        )
        # Emit the terminal decision + the run's cost, so the UI closes out the
        # run with its route, rationale, citations, and token/USD accounting.
        emitter = self._deps.emitter
        if emitter is not None:
            emitter.decision(  # type: ignore[attr-defined]
                route=route.value,
                rationale=rationale,
                citations=list(citations),
                actor=decided_by.value,
                gl_account=gl_coding.gl_account if gl_coding is not None else None,
            )
            if state.budget is not None:
                from ap_agent.llm.pricing import cost_usd

                emitter.cost(  # type: ignore[attr-defined]
                    input_tokens=state.budget.input_tokens,
                    output_tokens=state.budget.output_tokens,
                    usd=cost_usd(
                        input_tokens=state.budget.input_tokens,
                        output_tokens=state.budget.output_tokens,
                    ),
                )
        return state

    def _route_after_decide(self, state: RunState) -> str:
        """Detour to HITL only when the decision requires human approval.

        A pure read of the decision the decide node already made — every other
        route (auto-approve, hold, reject) terminates. HITL requires a
        checkpointer; if none is wired, the run cannot pause, so it terminates
        with the decision as-is (the escalation is recorded on the decision, and
        a caller that wants the pause must supply a checkpointer)."""
        if state.decision is None:  # pragma: no cover - decide always sets one
            return "done"
        if state.decision.route is not Route.ROUTE_FOR_APPROVAL:
            return "done"
        if self._deps.checkpointer is None:
            # No durable state to pause into. Do not fake an interrupt — leave
            # the ROUTE_FOR_APPROVAL decision standing for the caller to act on.
            return "done"
        return "hitl"

    def _hitl_node(self, state: RunState) -> RunState:
        """Pause for a human, then apply their decision (FR-11.1, FR-11.2).

        Runs only on the approval path. It records the pending review (the system
        of record), notifies the approver, then ``interrupt()``s — the run halts
        here until a caller resumes with a `ReviewDecision`. On resume the human's
        approve/edit/reject is applied to the terminal `Decision`, the review row
        and audit trail are updated (actor + timestamp), and the run completes.

        The approver is a human recorded as the actor of record; the agent is
        never the approver (FR-4.11), which the `Decision` model re-validates.
        """
        from langgraph.types import interrupt

        from ap_agent.agent.hitl import ReviewCard, ReviewDecision, ReviewDecisionType

        # On resume, LangGraph re-executes this node with state rehydrated from
        # the checkpoint, where nested models may arrive as plain dicts. Normalise
        # the fields this node reads back to typed models so the pause pass and
        # the resume pass behave identically.
        _rehydrate_state(state)

        decision = state.decision
        invoice = state.invoice
        if decision is None or invoice is None:  # pragma: no cover - guarded
            return state

        card = ReviewCard(
            invoice_id=invoice.invoice_id,
            tenant_id=state.tenant_id,
            reason=decision.rationale,
            proposed_route=decision.route.value,
            required_tier=decision.required_approver_tier,
            proposed_gl_account=(state.gl_coding.gl_account if state.gl_coding else None),
            amount=str(invoice.total_amount.value.amount),
            currency=invoice.total_amount.value.currency,
            citations=decision.citations,
        )

        # 1. Persist the pending review + notify the approver, before pausing, so
        #    the queue is the system of record even if the process dies while the
        #    human deliberates. Best-effort persistence: a demo without a DB still
        #    pauses and resumes (the row is the durable record when a DB exists).
        self._notify_and_record(state, card)

        # 2. Pause. The run halts here; the resume value becomes `raw`.
        raw = interrupt(card.as_dict())
        review_decision = ReviewDecision.from_resume(raw)

        # 3. Apply the human's decision to the terminal decision + record it.
        # The pause is over: the follow-up persist must store a finished run,
        # not another awaiting-review snapshot.
        state.pending_hitl = False
        self._apply_review_decision(state, review_decision)
        self._record_decision(state, review_decision)

        status_map = {
            ReviewDecisionType.APPROVE: "approved",
            ReviewDecisionType.EDIT: "edited",
            ReviewDecisionType.REJECT: "rejected",
        }
        state.hitl_status = status_map[review_decision.decision]
        return state

    def _notify_and_record(self, state: RunState, card: ReviewCard) -> None:
        """Notify the approver and persist the pending review row (best effort).

        The notification is the reviewer's channel; the row is the audit system
        of record. Both are attempted before the interrupt. Persistence is
        guarded so an in-memory demo (no DB session) still escalates and pauses.
        """
        # Notify (console notifier by default; Slice 9 MCP notify server or a
        # real Slack/email adapter behind the same shape). A notification failure
        # must not block the escalation — the review row is the record of truth.
        with contextlib.suppress(Exception):
            from ap_agent.agent.notify import get_notifier

            get_notifier().notify(
                tenant_id=state.tenant_id,
                required_tier=card.required_tier or "unspecified",
                reason=card.reason,
                invoice_id=card.invoice_id,
                amount=card.amount,
                currency=card.currency,
            )

        # The review row references runs.run_id, so the run, invoice, and
        # extraction have to be written first. Mark the pause before that
        # write so a restart sees awaiting_review rather than a finished run.
        # Best-effort: an in-memory demo (no DB session) still pauses.
        state.pending_hitl = True
        session_scope = self._deps.session_factory
        try:
            with session_scope() as session:  # type: ignore[operator]
                if session is not None:
                    from ap_agent.persistence.run_writer import persist_run

                    persist_run(session, state)
        except Exception:
            logging.getLogger(__name__).exception(
                "failed to persist paused HITL run %s", state.run_id
            )

    def _apply_review_decision(self, state: RunState, review: ReviewDecision) -> None:
        """Rebuild the terminal `Decision` from the human's approve/edit/reject.

        The human becomes the approver of record. Reject holds the invoice;
        approve routes for approval with the named approver; edit additionally
        applies the corrected GL coding. SoD is preserved: the actor is HUMAN and
        the approver identity is the human's, never the agent's.
        """
        from ap_agent.agent.hitl import ReviewDecisionType

        decision = state.decision
        invoice = state.invoice
        if decision is None or invoice is None:  # pragma: no cover
            return

        if review.decision is not ReviewDecisionType.REJECT and not review.approver_identity:
            # An approve/edit with no named actor cannot be the approver of
            # record. Fail loud rather than record an anonymous approval.
            raise HumanInputRequiredError(
                "A HITL approve/edit decision must name the approver (actor of "
                "record); none was supplied.",
                context=ErrorContext(stage="supervisor.hitl", tenant_id=state.tenant_id),
            )

        gl_coding = state.gl_coding
        if review.decision is ReviewDecisionType.EDIT and review.edited_gl_account:
            # Apply the human's corrected coding. A human-supplied code is
            # authoritative (it is not a model proposal needing a citation), so
            # it is recorded as groundable via a human decision.
            gl_coding = GLCoding(
                gl_account=review.edited_gl_account,
                cost_center=review.edited_cost_center,
                confidence=1.0,
                citations=(),
                reasoning=f"Corrected by approver {review.approver_identity}.",
                inherited_from_po=False,
            )
            state.gl_coding = gl_coding

        if review.decision is ReviewDecisionType.REJECT:
            route, actor, approver = Route.REJECT, Actor.HUMAN, review.approver_identity
            rationale = f"Rejected by {review.approver_identity or 'reviewer'}."
            if review.note:
                rationale = f"{rationale} {review.note}"
        else:
            route, actor, approver = (
                Route.ROUTE_FOR_APPROVAL,
                Actor.HUMAN,
                review.approver_identity,
            )
            verb = (
                "approved"
                if review.decision is ReviewDecisionType.APPROVE
                else "approved with edits"
            )
            rationale = f"{verb} by {review.approver_identity}."
            if review.note:
                rationale = f"{rationale} {review.note}"

        state.decision = Decision(
            invoice_id=invoice.invoice_id,
            route=route,
            decided_by=actor,
            rationale=rationale,
            decided_at=review.decided_at,
            gl_coding=gl_coding,
            required_approver_tier=decision.required_approver_tier,
            approver_identity=approver,
            citations=gl_coding.citations if gl_coding else (),
        )

    def _record_decision(self, state: RunState, review: ReviewDecision) -> None:
        """Persist the decision to the review row + audit trail (best effort)."""
        from ap_agent.agent.hitl import record_review_decision

        if state.hitl_review_id is None:
            return
        session_scope = self._deps.session_factory
        # Best-effort so a demo without a DB still resumes; with a DB this writes
        # the decision to the review row and the actor+timestamp audit entry.
        with contextlib.suppress(Exception), session_scope() as session:  # type: ignore[operator]
            if session is not None:
                record_review_decision(
                    session,
                    review_id=state.hitl_review_id,
                    tenant_id=state.tenant_id,
                    run_id=state.run_id,
                    invoice_id=state.invoice.invoice_id if state.invoice else "",
                    decision=review,
                )

    # --------------------------------------------------------------- helpers
    def _choose_route(
        self,
        *,
        gate_verdict: GateVerdict,
        gate_reason: str,
        gate_tier: str | None,
        coding_forces_human: bool,
        coding_result: GLCodingResult | None,
    ) -> tuple[Route, Actor, str | None, str]:
        if gate_verdict is GateVerdict.REJECT:
            return (Route.HOLD, POLICY_ACTOR, None, gate_reason)
        if gate_verdict is GateVerdict.REQUIRE_APPROVAL:
            return (Route.ROUTE_FOR_APPROVAL, AGENT_ACTOR, gate_tier, gate_reason)
        if coding_forces_human:
            reason = (
                coding_result.reasoning
                if coding_result is not None
                else "GL coding requires a human."
            )
            return (Route.ROUTE_FOR_APPROVAL, AGENT_ACTOR, gate_tier, reason)
        return (
            Route.AUTO_APPROVE,
            POLICY_ACTOR,
            None,
            f"Clean match, within touchless ceiling, coding grounded. {gate_reason}",
        )

    def _resolve_gl_coding(self, state: RunState, gate_ctx: PolicyGateContext) -> GLCoding | None:
        invoice = state.invoice
        if invoice is None:  # pragma: no cover
            return None
        if not state.is_non_po:
            # PO-backed: inherit coding from the matched PO (FR-11.4).
            po = gate_ctx.purchase_order
            account = getattr(po, "gl_account", None) if po is not None else None
            if account is None:
                return None
            return GLCoding(
                gl_account=account,
                cost_center=getattr(po, "cost_center", None),
                entity=getattr(po, "entity", None),
                confidence=1.0,
                citations=(),
                reasoning=f"Inherited from purchase order {getattr(po, 'po_number', '?')}.",
                inherited_from_po=True,
            )
        # Non-PO: use the grounded auto-code if the gate allowed it, else none.
        if state.coding_result is not None and state.coding_result.auto_applicable:
            return state.coding_result.coding
        return None

    def _load_gate_context(self, invoice: Invoice) -> PolicyGateContext:
        """Load the ERP counterparts for the policy engine (read side)."""
        po = None
        grn = None
        if not invoice.is_non_po and invoice.po_reference is not None:
            po_number = str(invoice.po_reference.value).strip()
            po = self._erp.get_purchase_order(po_number)
            grn = self._erp.get_goods_receipt(po_number)

        vendor_id = invoice.resolved_vendor_id
        vendor = self._erp.get_vendor(vendor_id) if vendor_id else None

        history: tuple[HistoricalBill, ...] = self._erp.list_historical_bills(
            vendor_id=vendor_id,
            since=_lookback_since(invoice, self._deps.policy),
        )
        return PolicyGateContext(
            invoice=invoice,
            policy=self._deps.policy,
            purchase_order=po,
            goods_receipt=grn,
            vendor=vendor,
            historical_bills=history,
            # The agent codes/routes; identities for SoD are supplied by the
            # human approval step (Slice 10), not the agent, by design.
            coder_identity=AGENT_ACTOR.value,
        )


# --------------------------------------------------------------- module helpers


def make_inmemory_checkpointer() -> object:
    """An `InMemorySaver` that can serialise this project's domain types.

    The checkpointer msgpack-serialises the run state between steps; our
    canonical enums and models are custom types, so the serializer is told to
    allow the project's modules explicitly (otherwise LangGraph warns now and
    will block in a future version). This is the in-process default for HITL
    demos and tests; a durable `PostgresSaver` (Slice 10 production) takes the
    same allow-list.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    # The allow-list is (module, class-name) pairs — the serializer matches on
    # the exact type it is asked to reconstruct, not the module alone. These are
    # every domain type that appears in a checkpointed RunState across a HITL
    # pause; missing one makes the serializer fall back (slow, and blocked in a
    # future LangGraph), so the set is explicit and complete.
    allowed = [
        ("ap_agent.core.canonical", "Invoice"),
        ("ap_agent.core.canonical", "InvoiceLine"),
        ("ap_agent.core.canonical", "InvoiceSource"),
        ("ap_agent.core.canonical", "Decision"),
        ("ap_agent.core.canonical", "GLCoding"),
        ("ap_agent.core.canonical", "Route"),
        ("ap_agent.core.canonical", "Actor"),
        ("ap_agent.core.primitives", "Money"),
        ("ap_agent.core.primitives", "Extracted"),
        ("ap_agent.core.primitives", "ExtractionMethod"),
        ("ap_agent.core.primitives", "SourceRegion"),
        ("ap_agent.core.primitives", "BoundingBox"),
        ("ap_agent.core.primitives", "RegionKind"),
        ("ap_agent.core.checks", "CheckResult"),
        ("ap_agent.core.checks", "CheckLedger"),
        ("ap_agent.core.checks", "CheckCategory"),
        ("ap_agent.core.checks", "Verdict"),
        ("ap_agent.core.checks", "Severity"),
        ("ap_agent.rag.coding", "GLCodingResult"),
        ("ap_agent.rag.coding", "FaithfulnessOutcome"),
        ("ap_agent.agent.state", "RunState"),
        ("ap_agent.agent.state", "RunBudget"),
    ]
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=allowed))


def node_span_check(result: Any) -> Any:
    """An OTEL span for one check, carrying its threshold/actual attributes.

    A thin adapter over `observability.tracing.check_span` so the policy node can
    wrap each check without importing tracing internals; a no-op when OTEL is
    unconfigured.
    """
    from ap_agent.observability.tracing import check_span

    return check_span(result.as_span_attributes())


def _rehydrate_state(state: RunState) -> None:
    """Coerce checkpoint-rehydrated dict fields back to typed models in place.

    When a run resumes from a checkpoint, LangGraph may hand back the run
    state's nested models (`Invoice`, `Decision`, `GLCoding`) as plain dicts. The
    HITL node re-executes on resume and needs the typed shapes it built on the
    pause pass, so this normalises them. A field already a model is left
    untouched; a dict is validated back into its model.
    """
    if isinstance(state.invoice, dict):
        state.invoice = Invoice.model_validate(state.invoice)
    if isinstance(state.decision, dict):
        state.decision = Decision.model_validate(state.decision)
    if isinstance(state.gl_coding, dict):
        state.gl_coding = GLCoding.model_validate(state.gl_coding)
    if isinstance(state.budget, dict):
        # RunBudget is a dataclass, not pydantic — reconstruct from its fields.
        known = {f.name for f in dataclasses_fields(RunBudget)}
        state.budget = RunBudget(**{k: v for k, v in state.budget.items() if k in known})


def _run_state_from_channels(channels: dict[str, object]) -> RunState:
    """Rebuild a `RunState` from LangGraph's dict of final channel values.

    LangGraph stores each dataclass field as a channel and returns them as a
    dict; only the fields the constructor accepts are passed through, so an
    unexpected channel cannot silently corrupt the typed state.
    """
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(RunState)}
    kwargs = {k: v for k, v in channels.items() if k in field_names}
    return RunState(**kwargs)  # type: ignore[arg-type]


def _default_budget() -> RunBudget:
    from ap_agent.config import get_settings

    s = get_settings()
    return RunBudget(
        max_model_calls=s.max_model_calls_per_run,
        max_tool_calls=s.max_tool_calls_per_run,
        max_tokens=s.max_tokens_per_run,
        max_seconds=s.max_run_seconds,
    )


def _lookback_since(invoice: Invoice, policy: PolicyPack) -> date:
    """The duplicate-detection lookback window start, relative to the invoice
    date (never wall clock), so a back-dated invoice is compared against the
    right window."""
    from datetime import timedelta

    return invoice.invoice_date.value - timedelta(days=policy.duplicates.lookback_days)


def _line_items_payload(invoice: Invoice) -> list[dict[str, Any]]:
    """The extracted line items as JSON-ready dicts for the extraction event.

    Line items are their own table on the UI's Extraction tab. Sourced from the
    canonical `Invoice.lines` via `InvoiceLine.to_provenance()` — the same shape
    persisted to `field_provenance`, so a reloaded run replays them identically.
    """
    return [line.to_provenance() for line in invoice.lines]


def new_run_state(
    *, tenant_id: str, invoice_id: str, document_id: str, invoice: Invoice | None = None
) -> RunState:
    """Construct a fresh run state with a generated run id."""
    state = RunState(
        tenant_id=tenant_id,
        run_id=str(uuid.uuid4()),
        invoice_id=invoice_id,
        document_id=document_id,
        invoice=invoice,
    )
    if invoice is not None:
        state.is_non_po = invoice.is_non_po
        state.as_of = invoice.invoice_date.value
    return state
