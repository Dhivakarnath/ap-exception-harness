"""The policy-engine gate — deterministic checks inside ``wrap_tool_call``
(FR-9.1).

This is the middleware that makes a write *earned*. Before ``post_erp_action``
executes, the gate assembles a `PolicyEvaluationContext` from what the run has
already loaded (the invoice, the tenant policy pack, the PO/GRN/vendor/history
fetched by the read tools) and runs the deterministic engine (`evaluate_all`).
It then translates the resulting `CheckLedger` — together with the DOA matrix
and segregation-of-duties rules — into one of three verdicts:

    * **allow**            — clean ledger, within the touchless ceiling: proceed
    * **require_approval** — a review-forcing check or an amount above the
                              ceiling: block the write and route to a human
    * **reject**           — a hard failure (blocked vendor, failed match):
                              refuse the write outright

Only ``allow`` lets the tool run. ``require_approval`` and ``reject`` both stop
the write; the difference is whether a human *could* approve it (require) or the
action is simply not permitted (reject). Both raise `PolicyViolationError`,
which the supervisor catches and turns into the terminal route — the gate's job
is to refuse, the supervisor's is to decide what the refusal means.

**Why a model can never talk past this.** The verdict is a pure function of the
deterministic ledger and the pack, computed in Python, with no model call. The
agent proposes the write; this gate disposes. A prompt cannot argue with it, and
the same refusal is re-enforced independently at the ERP boundary (the ledger
refuses a blocked vendor regardless) — defence in depth, Slice 9 adds the MCP
layer as the third.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

from ap_agent.agent.middleware import HarnessState
from ap_agent.core.canonical import Invoice
from ap_agent.core.checks import CheckLedger
from ap_agent.core.policy_pack import PolicyPack
from ap_agent.errors import ErrorContext, PolicyViolationError
from ap_agent.policy.context import HistoricalBill, PolicyEvaluationContext
from ap_agent.policy.engine import evaluate_all


class GateVerdict(StrEnum):
    """What the deterministic gate concluded about a proposed write."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The gate's verdict plus the evidence that produced it.

    Returned by `evaluate_gate` so the supervisor can both act on the verdict and
    record *why* — the ledger and the required approver tier — in the audit
    trail, without re-running the engine.
    """

    verdict: GateVerdict
    ledger: CheckLedger
    reason: str
    required_tier: str | None = None


def evaluate_gate(
    *,
    invoice: Invoice,
    policy: PolicyPack,
    ledger: CheckLedger,
) -> GateDecision:
    """Translate a computed check ledger + the pack into allow/require/reject.

    Pure function: given the deterministic ledger and the pack, the verdict is
    fixed and replayable. Kept separate from the middleware so the supervisor can
    call it directly on the PO-inherited path (where there is no tool call to
    wrap) and so it is trivially unit-testable without a graph.

    Order of precedence, strictest first:

    1. Any FAIL verdict → **reject**. A failed three-way match, a confirmed
       duplicate, a blocked vendor: the write must not proceed at all.
    2. Any review-forcing check (bank-detail change, uncited coding, threshold
       avoidance) or an amount above the touchless ceiling → **require_approval**.
    3. Otherwise (clean, within ceiling) → **allow**.
    """
    if ledger.failures:
        names = ", ".join(r.name for r in ledger.failures)
        return GateDecision(
            verdict=GateVerdict.REJECT,
            ledger=ledger,
            reason=f"Hard check failure(s): {names}. The write is refused.",
        )

    amount = invoice.total_amount.value
    within_ceiling = policy.is_within_touchless(amount)
    tier = policy.doa.tier_for(amount.amount).tier

    if ledger.review_forcing:
        names = ", ".join(r.name for r in ledger.review_forcing)
        return GateDecision(
            verdict=GateVerdict.REQUIRE_APPROVAL,
            ledger=ledger,
            reason=f"Review-forcing check(s): {names}. Routing to a human approver.",
            required_tier=tier,
        )

    if not within_ceiling:
        return GateDecision(
            verdict=GateVerdict.REQUIRE_APPROVAL,
            ledger=ledger,
            reason=(
                f"Amount {amount} exceeds the touchless ceiling "
                f"{policy.touchless_ceiling}. Routing to approver tier {tier!r}."
            ),
            required_tier=tier,
        )

    return GateDecision(
        verdict=GateVerdict.ALLOW,
        ledger=ledger,
        reason=(
            f"Clean ledger and amount {amount} within the touchless ceiling "
            f"{policy.touchless_ceiling}."
        ),
    )


@dataclass(slots=True)
class PolicyGateContext:
    """What the gate needs to evaluate a write, loaded by the run before the tool.

    The supervisor populates this once the read stages have run — the invoice,
    the pack, and the ERP counterparts — and hands it to the middleware. The gate
    itself fetches nothing (the engine is a pure function of its context); this
    object is the seam that carries the already-loaded facts in.
    """

    invoice: Invoice
    policy: PolicyPack
    purchase_order: Any | None = None
    goods_receipt: Any | None = None
    vendor: Any | None = None
    vendor_resolution: Any | None = None
    historical_bills: tuple[HistoricalBill, ...] = ()
    coder_identity: str | None = None
    approver_identity: str | None = None
    payer_identity: str | None = None

    def to_evaluation_context(self) -> PolicyEvaluationContext:
        return PolicyEvaluationContext(
            invoice=self.invoice,
            policy=self.policy,
            purchase_order=self.purchase_order,
            goods_receipt=self.goods_receipt,
            vendor=self.vendor,
            vendor_resolution=self.vendor_resolution,
            historical_bills=self.historical_bills,
            coder_identity=self.coder_identity,
            approver_identity=self.approver_identity,
            payer_identity=self.payer_identity,
        )


class PolicyEngineGateMiddleware(AgentMiddleware[HarnessState]):
    """Run the deterministic policy engine before any write tool executes.

    Wraps ``wrap_tool_call``: for a *write* tool (``post_erp_action``) it builds
    the evaluation context, runs `evaluate_all`, and only lets the handler
    proceed on an ``ALLOW`` verdict. ``REQUIRE_APPROVAL`` and ``REJECT`` both
    raise `PolicyViolationError` — the write does not happen. Read tools pass
    through untouched; there is nothing to gate on a read.

    The gate context is supplied per-run via `set_context`, because the invoice
    and its ERP counterparts are not known until the run's read stages have
    executed. Held on the instance only as read-only-after-set configuration for
    the current run; the supervisor builds one middleware per run.
    """

    name = "policy_engine_gate"

    # Tools that touch outside-world state and therefore must clear the engine.
    _WRITE_TOOLS = frozenset({"post_erp_action"})

    def __init__(self, *, context: PolicyGateContext | None = None) -> None:
        super().__init__()
        self._context = context

    def set_context(self, context: PolicyGateContext) -> None:
        self._context = context

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        tool_name = request.tool_call.get("name", "")
        if tool_name not in self._WRITE_TOOLS:
            return handler(request)

        if self._context is None:
            # A write reached the gate with no evaluation context loaded. That is
            # a wiring bug, and the safe direction is to refuse: never let a write
            # through un-evaluated.
            raise PolicyViolationError(
                "policy_gate_uninitialised",
                f"Write tool {tool_name!r} was called before the policy gate had "
                "an evaluation context. Refusing the write.",
                context=ErrorContext(stage="harness.policy_gate"),
            )

        ledger = evaluate_all(self._context.to_evaluation_context())
        decision = evaluate_gate(
            invoice=self._context.invoice,
            policy=self._context.policy,
            ledger=ledger,
        )

        if decision.verdict is not GateVerdict.ALLOW:
            raise PolicyViolationError(
                f"policy_gate_{decision.verdict.value}",
                decision.reason,
                context=ErrorContext(
                    stage="harness.policy_gate",
                    inputs={
                        "verdict": decision.verdict.value,
                        "required_tier": decision.required_tier,
                        "failures": [r.name for r in ledger.failures],
                        "review_forcing": [r.name for r in ledger.review_forcing],
                    },
                ),
            )
        return handler(request)
