"""Slice 8 — supervisor graph and harness (unit tier, no DB/model/network).

These tests assert the *load-bearing* properties of the harness, the ones that
would be a security or correctness incident if they regressed:

* the PO vs non-PO route is decided by the invoice, not a model;
* a forbidden tool is never even offered to a sub-agent (RBAC);
* the deterministic policy gate blocks a write it has not cleared;
* a run budget breach halts and escalates rather than truncating silently;
* an induced logic error propagates loud and typed, never masked;
* one sub-agent's context slice contains none of another's private inputs.

Everything runs against scripted doubles (a scripted coding model, an in-memory
ERP, a fake retriever), so the whole harness is exercised with no Bedrock, no
Postgres, and no network.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ap_agent.agent.context_slicing import SubAgent, coding_slice, context_utilisation
from ap_agent.agent.erp_client import InProcessErpClient
from ap_agent.agent.middleware import (
    BudgetMiddleware,
    RbacToolFilterMiddleware,
    ToolErrorMiddleware,
    _redact_pii,
)
from ap_agent.agent.policy_gate import GateVerdict, evaluate_gate
from ap_agent.agent.state import RunBudget
from ap_agent.agent.supervisor import Supervisor, SupervisorDeps, new_run_state
from ap_agent.agent.tools import ALL_TOOL_NAMES, MAX_TOOLS, ToolContext, build_toolset
from ap_agent.core.canonical import Invoice, InvoiceLine, Route
from ap_agent.core.checks import (
    CheckCategory,
    CheckLedger,
    CheckResult,
    Severity,
    Verdict,
)
from ap_agent.core.policy_pack import load_policy_pack
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money
from ap_agent.errors import APAgentError, BudgetExceededError
from ap_agent.rag.coding import RawGLProposal, ScriptedCodingModel
from tests.unit.rag_fakes import make_chunk

_POLICY_PACKS = Path(__file__).resolve().parents[2] / "policy_packs"


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def pack() -> Any:
    return load_policy_pack(_POLICY_PACKS / "retail_non_po.yaml")


def _ex(value: Any, confidence: float = 0.99) -> Extracted[Any]:
    return Extracted(value=value, confidence=confidence, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="USD")


def _invoice(*, total: str = "500.00", po: str | None = None, vendor_id: str = "V-1001") -> Invoice:
    return Invoice(
        invoice_id="inv-1",
        tenant_id="retail-demo",
        document_id="doc-1",
        invoice_number=_ex("INV-100"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex("Datamesh Analytics"),
        currency=_ex("USD"),
        subtotal=_ex(_money(total)),
        total_amount=_ex(_money(total)),
        po_reference=_ex(po) if po else None,
        resolved_vendor_id=vendor_id,
        lines=(
            InvoiceLine(
                line_number=1,
                description=_ex("Analytics platform annual subscription"),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(_money(total)),
                line_total=_ex(_money(total)),
            ),
        ),
    )


class _FakeRetriever:
    """Returns one grounded, lexically-anchored chunk for any query."""

    def __init__(self, chunks: list[Any] | None = None) -> None:
        self._chunks = (
            chunks
            if chunks is not None
            else [
                make_chunk(
                    citation_ref="policy/gl_coding_policy_software_subscriptions.md#1",
                    content="SaaS subscriptions code to 7200",
                    lexical_rank=1,
                    rrf_score=0.05,
                )
            ]
        )

    def retrieve(self, session: Any, **kwargs: Any) -> list[Any]:
        return self._chunks


@contextmanager
def _fake_session() -> Any:
    yield None


def _deps(pack: Any, *, retriever: Any = None, coding: Any = None) -> SupervisorDeps:
    return SupervisorDeps(
        tenant_id="retail-demo",
        policy=pack,
        erp=InProcessErpClient(),
        retriever=retriever or _FakeRetriever(),
        coding_model=coding
        or ScriptedCodingModel(
            RawGLProposal(
                gl_account="7200",
                confidence=0.95,
                citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",),
            )
        ),
        session_factory=_fake_session,
    )


def _run(deps: SupervisorDeps, invoice: Invoice) -> Any:
    state = new_run_state(
        tenant_id="retail-demo", invoice_id=invoice.invoice_id, document_id="doc-1", invoice=invoice
    )
    return Supervisor(deps).run(state)


# --------------------------------------------------------------- routing


class TestRouting:
    def test_non_po_invoice_takes_the_non_po_branch(self, pack: Any) -> None:
        out = _run(_deps(pack), _invoice(po=None))
        assert out.is_non_po is True
        # A grounded, small, clean non-PO invoice auto-approves with its code.
        assert out.route is Route.AUTO_APPROVE
        assert out.gl_coding is not None
        assert out.gl_coding.gl_account == "7200"
        assert {call["name"] for call in out.tool_trace} == {
            "erp.get_vendor",
            "erp.list_historical_bills",
        }

    def test_po_backed_invoice_takes_the_po_branch_and_inherits(self, pack: Any) -> None:
        # PO-2001 is seeded; its coding is inherited rather than proposed.
        out = _run(_deps(pack), _invoice(total="2400.00", po="PO-2001"))
        assert out.is_non_po is False
        assert out.gl_coding is not None
        assert out.gl_coding.inherited_from_po is True
        assert {
            "erp.get_purchase_order",
            "erp.get_goods_receipt",
            "erp.get_vendor",
            "erp.list_historical_bills",
        }.issubset({call["name"] for call in out.tool_trace})

    def test_routing_is_a_pure_property_not_a_model_call(self, pack: Any) -> None:
        # The coding model must never be consulted to decide the branch: a PO
        # invoice does not even enter the coding node. Assert by giving a coding
        # model that would raise if called, on the PO path.
        class _ExplodingCoding:
            def propose_coding(self, **_: Any) -> RawGLProposal:
                raise AssertionError("coding model must not run on the PO path")

        out = _run(_deps(pack, coding=_ExplodingCoding()), _invoice(total="2400.00", po="PO-2001"))
        # Reached a decision without the coding model raising.
        assert out.decision is not None


class TestNonPoCodingOutcomes:
    def test_ungrounded_coding_routes_to_a_human(self, pack: Any) -> None:
        # Empty retrieval -> no context to ground -> HITL, never an auto-code.
        out = _run(_deps(pack, retriever=_FakeRetriever(chunks=[])), _invoice(po=None))
        assert out.route is Route.ROUTE_FOR_APPROVAL
        assert out.gl_coding is None

    def test_large_amount_routes_for_approval_even_when_coded(self, pack: Any) -> None:
        out = _run(_deps(pack), _invoice(total="5000.00", po=None))
        assert out.route is Route.ROUTE_FOR_APPROVAL
        assert out.decision is not None
        assert out.decision.required_approver_tier is not None


# --------------------------------------------------------------- RBAC


class TestRbacToolFiltering:
    def test_toolset_never_exceeds_the_cap(self) -> None:
        ctx = ToolContext(
            tenant_id="retail-demo",
            erp=InProcessErpClient(),
            retriever=None,  # type: ignore[arg-type]
            extractor=None,  # type: ignore[arg-type]
            session_factory=_fake_session,
        )
        assert len(build_toolset(ctx)) <= MAX_TOOLS

    def test_rbac_filter_removes_forbidden_tools_before_the_model(self) -> None:
        # A read-only sub-agent must not even be offered the write tools.
        from ap_agent.agent.tools import READ_ONLY_TOOL_NAMES

        ctx = ToolContext(
            tenant_id="retail-demo",
            erp=InProcessErpClient(),
            retriever=None,  # type: ignore[arg-type]
            extractor=None,  # type: ignore[arg-type]
            session_factory=_fake_session,
        )
        all_tools = build_toolset(ctx)
        mw = RbacToolFilterMiddleware(allowed_tool_names=READ_ONLY_TOOL_NAMES)

        captured: dict[str, Any] = {}

        class _Req:
            tools = all_tools

            def override(self, *, tools: list[Any]) -> Any:
                captured["tools"] = tools
                return self

        def _handler(_req: Any) -> Any:
            return None

        mw.wrap_model_call(_Req(), _handler)  # type: ignore[arg-type]
        offered = {t.name for t in captured["tools"]}
        assert "post_erp_action" not in offered
        assert "notify_approver" not in offered
        assert offered <= READ_ONLY_TOOL_NAMES


# --------------------------------------------------------------- policy gate


class TestPolicyGateBlocksWrites:
    def test_reject_verdict_from_a_failure(self, pack: Any) -> None:
        ledger = CheckLedger(
            results=(
                CheckResult(
                    name="three_way_match",
                    category=CheckCategory.MATCHING,
                    verdict=Verdict.FAIL,
                    severity=Severity.HIGH,
                    reasoning="quantity mismatch",
                ),
            )
        )
        decision = evaluate_gate(invoice=_invoice(), policy=pack, ledger=ledger)
        assert decision.verdict is GateVerdict.REJECT

    def test_require_approval_over_ceiling(self, pack: Any) -> None:
        decision = evaluate_gate(invoice=_invoice(total="9000.00"), policy=pack, ledger=CheckLedger())
        assert decision.verdict is GateVerdict.REQUIRE_APPROVAL
        assert decision.required_tier is not None

    def test_allow_when_clean_and_within_ceiling(self, pack: Any) -> None:
        decision = evaluate_gate(invoice=_invoice(total="500.00"), policy=pack, ledger=CheckLedger())
        assert decision.verdict is GateVerdict.ALLOW

    def test_a_failed_match_holds_the_run(self, pack: Any) -> None:
        # PO-backed with a mismatching synthetic line -> match FAIL -> HOLD.
        out = _run(_deps(pack), _invoice(total="2400.00", po="PO-2001"))
        assert out.route is Route.HOLD


# --------------------------------------------------------------- budgets


class TestBudgetsHaltAndEscalate:
    def test_wall_clock_breach_halts_the_run(self, pack: Any) -> None:
        state = new_run_state(
            tenant_id="retail-demo", invoice_id="inv-1", document_id="doc-1", invoice=_invoice()
        )
        state.budget = RunBudget(
            max_model_calls=25, max_tool_calls=40, max_tokens=120_000, max_seconds=0
        )
        out = Supervisor(_deps(pack)).run(state)
        assert out.route is None  # no decision produced
        assert out.error is not None
        assert out.error["error_type"] == "BudgetExceededError"

    def test_token_breach_halts_the_run(self, pack: Any) -> None:
        state = new_run_state(
            tenant_id="retail-demo", invoice_id="inv-1", document_id="doc-1", invoice=_invoice()
        )
        state.budget = RunBudget(
            max_model_calls=25, max_tool_calls=40, max_tokens=1, max_seconds=180
        )
        state.budget.input_tokens = 5  # already over the 1-token ceiling
        out = Supervisor(_deps(pack)).run(state)
        assert out.error is not None
        assert out.error["error_class"] == "budget"

    def test_budget_middleware_raises_before_the_call(self) -> None:
        mw = BudgetMiddleware(
            max_model_calls=1, max_tool_calls=1, max_tokens=1000, max_seconds=180
        )

        class _State(dict[str, Any]):
            pass

        state = _State(model_calls=1)  # already at the limit

        class _Req:
            def __init__(self, s: Any) -> None:
                self.state = s

        with pytest.raises(BudgetExceededError):
            mw.wrap_model_call(_Req(state), lambda _r: None)  # type: ignore[arg-type]


# --------------------------------------------------------------- fail loud


class TestFailLoud:
    def test_tool_error_middleware_wraps_unexpected_into_typed_fatal(self) -> None:
        mw = ToolErrorMiddleware()

        class _Req:
            tool_call = {"name": "get_purchase_order", "id": "1"}

        def _boom(_req: Any) -> Any:
            raise RuntimeError("kaboom")

        with pytest.raises(APAgentError) as exc:
            mw.wrap_tool_call(_Req(), _boom)  # type: ignore[arg-type]
        # Wrapped, not swallowed; the original cause is preserved.
        assert "kaboom" in str(exc.value.cause)

    def test_first_party_error_propagates_unchanged(self) -> None:
        mw = ToolErrorMiddleware()

        class _Req:
            tool_call = {"name": "post_erp_action", "id": "1"}

        original = APAgentError("already typed")

        def _raise(_req: Any) -> Any:
            raise original

        with pytest.raises(APAgentError) as exc:
            mw.wrap_tool_call(_Req(), _raise)  # type: ignore[arg-type]
        # Same object — the middleware did not re-wrap a first-party error.
        assert exc.value is original

    def test_induced_logic_error_in_a_node_propagates(self, pack: Any) -> None:
        # A retriever that raises a logic error (not transient) must surface as a
        # loud run error, never a silent empty coding.
        class _BrokenRetriever:
            def retrieve(self, session: Any, **kwargs: Any) -> list[Any]:
                raise ValueError("retriever logic bug")

        deps = _deps(pack, retriever=_BrokenRetriever())
        # The ValueError is not an APAgentError, so run() does not catch it — it
        # propagates, which is the fail-loud contract for a genuine logic bug.
        with pytest.raises(ValueError, match="retriever logic bug"):
            _run(deps, _invoice(po=None))


# --------------------------------------------------------------- PII


class TestPiiRedaction:
    def test_long_account_numbers_are_masked(self) -> None:
        assert "[REDACTED-3456]" in _redact_pii("remit to 1234 5678 9012 3456 now")

    def test_short_identifiers_are_left_intact(self) -> None:
        text = "invoice INV-001 PO PO-2001 account 6500"
        assert _redact_pii(text) == text


# --------------------------------------------------------------- context slicing


class TestContextSlicing:
    def test_coding_slice_contains_only_vendor_lines_total(self, pack: Any) -> None:
        sliced = coding_slice(_invoice(total="500.00"), [])
        assert "Datamesh" in sliced.invoice_summary
        assert "Analytics platform" in sliced.invoice_summary
        # No policy pack, no ledger, no ERP record leaks into the coding slice.
        for forbidden in sliced.forbidden_inputs:
            assert forbidden not in sliced.invoice_summary

    def test_coding_slice_never_carries_policy_or_erp_inputs(self) -> None:
        # The slice's own contract lists what it must never contain.
        sliced = coding_slice(_invoice(), [])
        assert "policy_pack" in sliced.forbidden_inputs
        assert "purchase_order" in sliced.forbidden_inputs
        assert "check_ledger" in sliced.forbidden_inputs

    def test_context_utilisation_is_a_fraction_of_budget(self) -> None:
        assert context_utilisation(tokens_used=600, token_budget=120_000) == pytest.approx(0.005)
        assert context_utilisation(tokens_used=0, token_budget=0) is None

    def test_sub_agents_are_named(self) -> None:
        assert SubAgent.EXTRACTION.value == "extraction"
        assert SubAgent.GL_CODING.value == "gl_coding"


# --------------------------------------------------------------- toolset shape


class TestToolsetShape:
    def test_all_tool_names_partition_read_and_write(self) -> None:
        from ap_agent.agent.tools import READ_ONLY_TOOL_NAMES, WRITE_TOOL_NAMES

        assert READ_ONLY_TOOL_NAMES.isdisjoint(WRITE_TOOL_NAMES)
        assert ALL_TOOL_NAMES == READ_ONLY_TOOL_NAMES | WRITE_TOOL_NAMES

    def test_no_payment_tool_exists(self) -> None:
        # The toolset terminates at "approved for payment": no tool moves money.
        forbidden = {"pay", "disburse", "execute_payment", "remit", "send_payment"}
        assert ALL_TOOL_NAMES.isdisjoint(forbidden)
