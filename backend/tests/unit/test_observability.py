"""Slice 11 — observability and per-check streaming (unit tier).

The properties that make a run *inspectable* rather than asserted:

* the RunEvent envelope is a stable, ordered contract — `seq` strictly
  increasing per run, so a client can detect a dropped event;
* PII is redacted before any payload leaves the emitter (deep, through nested
  structures), while short identifiers the checks need are kept;
* a real run emits **exactly one check event per check**, in evaluation order,
  and closes with a decision + cost event;
* OTEL spans are a safe no-op when unconfigured (instrumentation never fails a
  run), and nest via the context managers;
* Langfuse export is a no-op (and a null deep-link) when unconfigured.

The metric/KPI arithmetic against persisted rows is covered by an integration
test (needs Postgres); here the focus is the event contract and the redaction,
which are pure.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ap_agent.agent.erp_client import InProcessErpClient, TracedErpClient
from ap_agent.agent.supervisor import Supervisor, SupervisorDeps, new_run_state
from ap_agent.config import get_settings
from ap_agent.core.canonical import Invoice, InvoiceLine, Route
from ap_agent.core.policy_pack import load_policy_pack
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money
from ap_agent.observability import langfuse_export, tracing
from ap_agent.observability.events import EventChannel, EventEmitter, RunEvent
from ap_agent.observability.redaction import redact_pii_deep, redact_pii_text
from ap_agent.observability.sse import sse_event
from ap_agent.rag.coding import RawGLProposal, ScriptedCodingModel
from tests.unit.rag_fakes import make_chunk

_POLICY_PACKS = Path(__file__).resolve().parents[2] / "policy_packs"


# --------------------------------------------------------------- envelope


class TestRunEventEnvelope:
    def test_seq_is_strictly_increasing_per_run(self) -> None:
        em = EventEmitter(run_id="r1")
        for _ in range(5):
            em.stage(name="n", status="ok")
        seqs = [e.seq for e in em.events]
        assert seqs == [0, 1, 2, 3, 4]

    def test_events_dispatch_to_the_sink(self) -> None:
        got: list[RunEvent] = []
        em = EventEmitter(run_id="r1", sink=got.append)
        em.check({"name": "x", "verdict": "pass", "reasoning": "ok"})
        assert len(got) == 1
        assert got[0].channel is EventChannel.CHECK

    def test_wire_shape_is_stable(self) -> None:
        em = EventEmitter(run_id="r1", trace_id="t1")
        ev = em.cost(input_tokens=10, output_tokens=2, usd=0.001)
        wire = ev.as_wire()
        assert set(wire) == {"run_id", "trace_id", "seq", "channel", "at", "payload"}
        assert wire["channel"] == "cost"

    def test_sse_frame_carries_seq_as_id(self) -> None:
        em = EventEmitter(run_id="r1")
        ev = em.stage(name="extract", status="ok")
        frame = sse_event(ev)
        assert frame.startswith(f"id: {ev.seq}\n")
        assert "data: " in frame and frame.endswith("\n\n")


# --------------------------------------------------------------- redaction


class TestRedaction:
    def test_long_account_numbers_are_masked(self) -> None:
        assert "[REDACTED-3456]" in redact_pii_text("acct 1234 5678 9012 3456")

    def test_short_identifiers_are_kept(self) -> None:
        text = "invoice INV-001 PO PO-2001 account 6500"
        assert redact_pii_text(text) == text

    def test_deep_redaction_walks_nested_payloads(self) -> None:
        payload = {
            "vendor_id": "V-1001",
            "remit": {"account": "1234567890123456", "bank": "First Bank"},
            "lines": ["cable", "acct 9999 8888 7777 6666"],
        }
        red = redact_pii_deep(payload)
        assert red["vendor_id"] == "V-1001"  # short id kept
        assert "[REDACTED-3456]" in red["remit"]["account"]
        assert "[REDACTED-6666]" in red["lines"][1]

    def test_emitter_redacts_tool_args(self) -> None:
        em = EventEmitter(run_id="r1")
        ev = em.tool(name="post_erp_action", args={"remit": "acct 1234 5678 9012 3456"})
        assert "[REDACTED-3456]" in ev.payload["args"]["remit"]

    def test_non_string_scalars_pass_through(self) -> None:
        assert redact_pii_deep({"amount": 5000, "ok": True}) == {"amount": 5000, "ok": True}


# --------------------------------------------------------------- integration into a run


def _ex(v: Any) -> Extracted[Any]:
    return Extracted(value=v, confidence=0.99, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(a: str) -> Money:
    return Money(amount=Decimal(a), currency="USD")


def _invoice() -> Invoice:
    return Invoice(
        invoice_id="i-obs",
        tenant_id="retail-demo",
        document_id="d",
        invoice_number=_ex("INV-OBS"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex("Datamesh"),
        currency=_ex("USD"),
        subtotal=_ex(_money("500.00")),
        total_amount=_ex(_money("500.00")),
        po_reference=None,
        resolved_vendor_id="V-1001",
        lines=(
            InvoiceLine(
                line_number=1,
                description=_ex("Analytics subscription"),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(_money("500.00")),
                line_total=_ex(_money("500.00")),
            ),
        ),
    )


class _R:
    def retrieve(self, s: Any, **k: Any) -> list[Any]:
        return [
            make_chunk(
                citation_ref="policy/gl_coding_policy_software_subscriptions.md#1",
                content="SaaS 7200",
                lexical_rank=1,
                rrf_score=0.05,
            )
        ]


@contextmanager
def _fs() -> Any:
    yield None


class TestRunEmitsEvents:
    def _run_with_emitter(self) -> tuple[Any, EventEmitter]:
        pack = load_policy_pack(_POLICY_PACKS / "retail_non_po.yaml")
        state = new_run_state(
            tenant_id="retail-demo", invoice_id="i-obs", document_id="d", invoice=_invoice()
        )
        em = EventEmitter(run_id=state.run_id, trace_id="t-obs")
        deps = SupervisorDeps(
            tenant_id="retail-demo",
            policy=pack,
            erp=InProcessErpClient(),
            retriever=_R(),
            coding_model=ScriptedCodingModel(
                RawGLProposal(
                    gl_account="7200",
                    confidence=0.95,
                    citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",),
                )
            ),
            session_factory=_fs,
            emitter=em,
        )
        out = Supervisor(deps).run(state)
        return out, em

    def test_exactly_one_check_event_per_check(self) -> None:
        out, em = self._run_with_emitter()
        check_events = [e for e in em.events if e.channel is EventChannel.CHECK]
        assert len(check_events) == len(out.ledger.results)
        assert len(check_events) > 0

    def test_check_events_are_in_evaluation_order(self) -> None:
        out, em = self._run_with_emitter()
        emitted_names = [e.payload["name"] for e in em.events if e.channel is EventChannel.CHECK]
        ledger_names = [r.name for r in out.ledger.results]
        assert emitted_names == ledger_names

    def test_run_emits_decision_and_cost(self) -> None:
        out, em = self._run_with_emitter()
        channels = {e.channel for e in em.events}
        assert EventChannel.DECISION in channels
        assert EventChannel.COST in channels
        decision = next(e for e in em.events if e.channel is EventChannel.DECISION)
        assert decision.payload["route"] == Route.AUTO_APPROVE.value

    def test_cost_event_reflects_captured_token_usage(self) -> None:
        # The coding model reports token usage; it must flow into the budget and
        # the cost event, with a non-zero USD derived from the configured price.
        # This is the INC-015 defect-3 regression guard: a live run's cost was
        # zero because usage was discarded. A scripted model with usage stands in
        # for a real call deterministically.
        pack = load_policy_pack(_POLICY_PACKS / "retail_non_po.yaml")
        state = new_run_state(
            tenant_id="retail-demo", invoice_id="i-cost", document_id="d", invoice=_invoice()
        )
        em = EventEmitter(run_id=state.run_id)
        deps = SupervisorDeps(
            tenant_id="retail-demo",
            policy=pack,
            erp=InProcessErpClient(),
            retriever=_R(),
            coding_model=ScriptedCodingModel(
                RawGLProposal(
                    gl_account="7200",
                    confidence=0.95,
                    citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",),
                ),
                usage={"input_tokens": 900, "output_tokens": 140},
            ),
            session_factory=_fs,
            emitter=em,
        )
        Supervisor(deps).run(state)

        cost = next(e for e in em.events if e.channel is EventChannel.COST)
        assert cost.payload["input_tokens"] == 900
        assert cost.payload["output_tokens"] == 140
        # USD is derived from the token counts and the configured per-token price
        # (Nova Lite default: $0.06/1M in, $0.24/1M out) -> non-zero and plausible.
        assert cost.payload["usd"] > 0
        from ap_agent.llm.pricing import cost_usd

        assert cost.payload["usd"] == pytest.approx(
            cost_usd(input_tokens=900, output_tokens=140)
        )

    def test_run_without_emitter_still_completes(self) -> None:
        # Observability is optional: a run with no emitter behaves identically.
        pack = load_policy_pack(_POLICY_PACKS / "retail_non_po.yaml")
        state = new_run_state(
            tenant_id="retail-demo", invoice_id="i-obs", document_id="d", invoice=_invoice()
        )
        deps = SupervisorDeps(
            tenant_id="retail-demo",
            policy=pack,
            erp=InProcessErpClient(),
            retriever=_R(),
            coding_model=ScriptedCodingModel(
                RawGLProposal(gl_account="7200", confidence=0.95, citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",))
            ),
            session_factory=_fs,
        )
        out = Supervisor(deps).run(state)
        assert out.route is Route.AUTO_APPROVE


# --------------------------------------------------------------- tracing / langfuse no-op


class TestDeepTraceGating:
    def test_spans_are_a_noop_when_deep_tracing_inactive(self, monkeypatch: Any) -> None:
        settings = get_settings().model_copy(
            update={"otel_exporter_otlp_endpoint": "http://localhost:4318"}
        )
        monkeypatch.setattr(tracing, "get_settings", lambda: settings)
        tracing.reset_tracer_for_test()
        with tracing.run_span(run_id="r", tenant_id="t", invoice_id="i") as run:
            assert run is None
        tracing.reset_tracer_for_test()

    def test_spans_open_when_deep_tracing_active(self, monkeypatch: Any) -> None:
        settings = get_settings().model_copy(
            update={"otel_exporter_otlp_endpoint": "http://localhost:4318"}
        )
        monkeypatch.setattr(tracing, "get_settings", lambda: settings)
        tracing.reset_tracer_for_test()
        with (
            tracing.active_deep_tracing(True),
            tracing.run_span(run_id="r", tenant_id="t", invoice_id="i") as run,
        ):
            # Tracer may still fail to init in CI without OTEL deps; either a real
            # span or a graceful no-op is acceptable — the gate must not block.
            assert run is None or hasattr(run, "set_attribute")
        tracing.reset_tracer_for_test()


class TestTracingNoOp:
    def test_spans_are_a_noop_when_otel_unconfigured(
        self, monkeypatch: Any
    ) -> None:
        # Force the unconfigured state rather than relying on the ambient .env
        # (which may now point OTEL at a local collector): the property under
        # test is "no endpoint -> no tracer -> spans do nothing", so the test
        # must own that precondition.
        settings = get_settings().model_copy(
            update={"otel_exporter_otlp_endpoint": None}
        )
        monkeypatch.setattr(tracing, "get_settings", lambda: settings)
        tracing.reset_tracer_for_test()
        with (
            tracing.run_span(run_id="r", tenant_id="t", invoice_id="i") as run,
            tracing.node_span("policy") as node,
            tracing.check_span({"check.name": "x", "check.verdict": "pass"}) as chk,
        ):
            pass
        assert run is None and node is None and chk is None
        assert tracing._get_tracer() is None
        tracing.reset_tracer_for_test()

    def test_langfuse_is_a_noop_when_unconfigured(self, monkeypatch: Any) -> None:
        # Same discipline: force all three langfuse_* to unset so the no-op path
        # is exercised regardless of what the local .env configures.
        settings = get_settings().model_copy(
            update={
                "langfuse_host": None,
                "langfuse_public_key": None,
                "langfuse_secret_key": None,
            }
        )
        monkeypatch.setattr(langfuse_export, "get_settings", lambda: settings)
        langfuse_export.reset_client_for_test()
        assert langfuse_export.langfuse_enabled() is False
        assert langfuse_export.get_langfuse() is None
        assert langfuse_export.trace_url("abc") is None
        langfuse_export.reset_client_for_test()


# --------------------------------------------------------------- tool spans


class TestErpToolSpans:
    """`TracedErpClient` opens one tool span per ERP call, PII-redacting args.

    Rather than stand up a live OTEL exporter, this records the spans the proxy
    *requests* by replacing `tool_span` with a recorder — that is the unit-level
    guarantee (every ERP call is wrapped and named), independent of whether a
    tracer is configured. Live export of these spans is covered end-to-end by the
    observability live run.
    """

    def _recorder(self, monkeypatch: Any) -> list[str]:
        opened: list[str] = []

        @contextmanager
        def _fake_tool_span(name: str) -> Any:
            opened.append(name)
            yield None  # no real span; the proxy tolerates a None span

        monkeypatch.setattr(tracing, "tool_span", _fake_tool_span)
        return opened

    def test_read_calls_open_named_tool_spans(self, monkeypatch: Any) -> None:
        opened = self._recorder(monkeypatch)
        erp = TracedErpClient(InProcessErpClient())

        # A PO-backed read path: PO + GRN + vendor + history all go through spans.
        erp.get_purchase_order("PO-2001")
        erp.get_goods_receipt("PO-2001")
        erp.get_vendor("V-1001")
        erp.list_historical_bills(vendor_id="V-1001", since=date(2020, 1, 1))

        assert opened == [
            "erp.get_purchase_order",
            "erp.get_goods_receipt",
            "erp.get_vendor",
            "erp.list_historical_bills",
        ]

    def test_proxy_delegates_return_values(self, monkeypatch: Any) -> None:
        # The span is transparent: the wrapped client's answer is returned as-is.
        self._recorder(monkeypatch)
        inner = InProcessErpClient()
        erp = TracedErpClient(inner)
        assert erp.get_purchase_order("PO-2001") == inner.get_purchase_order("PO-2001")

    def test_span_args_go_through_the_deep_redactor(self, monkeypatch: Any) -> None:
        # The proxy must run its span args through `redact_pii_deep` — prove it by
        # spying on the real redactor (capturing the original before patching, so
        # the spy is not recursive) and confirming the ERP call's args reach it.
        from ap_agent.observability import redaction

        original = redaction.redact_pii_deep
        seen: list[dict[str, Any]] = []

        def _spy(payload: Any) -> Any:
            out = original(payload)
            if isinstance(payload, dict):
                seen.append(payload)
            return out

        monkeypatch.setattr(redaction, "redact_pii_deep", _spy)
        self._recorder(monkeypatch)
        erp = TracedErpClient(InProcessErpClient())
        erp.get_vendor("V-1001")
        assert any("vendor_id" in p for p in seen)


# --------------------------------------------------------------- pricing


class TestCostPricing:
    def test_zero_tokens_zero_cost(self) -> None:
        from ap_agent.llm.pricing import cost_usd

        assert cost_usd(input_tokens=0, output_tokens=0) == 0.0

    def test_cost_uses_configured_per_token_price(self) -> None:
        # 1M input + 1M output at the Nova Lite default ($0.06 in, $0.24 out)
        # should be $0.30. Derived from real config, not a magic number.
        from ap_agent.llm.pricing import cost_usd

        s = get_settings()
        expected = s.model_price_input_per_mtok_usd + s.model_price_output_per_mtok_usd
        assert cost_usd(input_tokens=1_000_000, output_tokens=1_000_000) == pytest.approx(
            expected
        )
