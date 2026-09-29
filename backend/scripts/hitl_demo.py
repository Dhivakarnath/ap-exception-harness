"""Demo: a high-value invoice pauses, notifies, waits, and resumes on approval.

    uv run python scripts/hitl_demo.py                 # approve
    uv run python scripts/hitl_demo.py --decision reject
    uv run python scripts/hitl_demo.py --decision edit --gl 6500

The Slice 10 demo checkpoint. A $5,000 non-PO invoice is over the retail
touchless ceiling, so the deterministic pipeline routes it for approval — and
with a checkpointer wired, the run **pauses at the HITL interrupt** rather than
finishing. The script prints the review card the interrupt surfaced, then
resumes the paused run with a human decision (approve / edit / reject) and prints
the completed decision, with the human recorded as the approver of record.

Runs in-process with scripted models — no AWS, no Postgres. The pause and resume
are the real LangGraph interrupt/`Command(resume=...)` machinery over an
in-memory checkpointer; a production deployment swaps in a durable checkpointer
and the `HitlReview` row is the system of record.
"""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "mock_erp"))

from ap_agent.agent.erp_client import InProcessErpClient  # noqa: E402
from ap_agent.agent.hitl import ReviewDecision, ReviewDecisionType  # noqa: E402
from ap_agent.agent.supervisor import (  # noqa: E402
    Supervisor,
    SupervisorDeps,
    make_inmemory_checkpointer,
    new_run_state,
)
from ap_agent.core.canonical import Invoice, InvoiceLine  # noqa: E402
from ap_agent.core.policy_pack import load_policy_pack  # noqa: E402
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money  # noqa: E402
from ap_agent.rag.coding import RawGLProposal, ScriptedCodingModel  # noqa: E402

POLICY_PACKS = REPO_ROOT / "backend" / "policy_packs"


def _ex(value: Any) -> Extracted[Any]:
    return Extracted(value=value, confidence=0.99, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(a: str) -> Money:
    return Money(amount=Decimal(a), currency="USD")


def _invoice() -> Invoice:
    return Invoice(
        invoice_id="demo-hitl",
        tenant_id="retail-demo",
        document_id="demo-doc",
        invoice_number=_ex("INV-BIG"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex("Datamesh Analytics"),
        currency=_ex("USD"),
        subtotal=_ex(_money("5000.00")),
        total_amount=_ex(_money("5000.00")),
        po_reference=None,
        resolved_vendor_id="V-1001",
        lines=(
            InvoiceLine(
                line_number=1,
                description=_ex("Analytics platform annual subscription"),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(_money("5000.00")),
                line_total=_ex(_money("5000.00")),
            ),
        ),
    )


class _FakeRetriever:
    def retrieve(self, session: Any, **kwargs: Any) -> list[Any]:
        from tests.unit.rag_fakes import make_chunk

        return [
            make_chunk(
                citation_ref="policy/gl_coding_policy_software_subscriptions.md#1",
                content="SaaS subscriptions code to 7200",
                lexical_rank=1,
                rrf_score=0.05,
            )
        ]


@contextmanager
def _fake_session() -> Any:
    yield None


def _build_deps(pack: Any, *, live: bool, mcp: bool) -> SupervisorDeps:
    """HITL supervisor deps, with a checkpointer so the run can pause/resume.

    Scripted+in-process by default; ``--live`` swaps in real Bedrock Nova Lite
    coding + Titan hybrid retrieval over the Postgres corpus, and ``--mcp`` routes
    the ERP through the real MCP stdio server. The HITL pause/resume is identical
    across all combinations — it is deterministic control flow with no model in
    it — so a live run proves the interrupt survives on top of real inference.
    """
    checkpointer = make_inmemory_checkpointer()
    erp: Any
    if mcp:
        from ap_agent.mcp_servers.erp_mcp_client import McpErpClient

        erp = McpErpClient()
    else:
        erp = InProcessErpClient()

    if live:
        from ap_agent.persistence.db import session_scope
        from ap_agent.rag.coding import BedrockCodingModel
        from ap_agent.rag.retriever import HybridRetriever

        return SupervisorDeps(
            tenant_id="retail-demo",
            policy=pack,
            erp=erp,
            retriever=HybridRetriever(),
            coding_model=BedrockCodingModel(),
            session_factory=session_scope,
            checkpointer=checkpointer,
        )
    return SupervisorDeps(
        tenant_id="retail-demo",
        policy=pack,
        erp=erp,
        retriever=cast("Any", _FakeRetriever()),  # duck-typed stand-in
        coding_model=ScriptedCodingModel(
            RawGLProposal(
                gl_account="7200",
                confidence=0.95,
                citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",),
            )
        ),
        session_factory=_fake_session,
        checkpointer=checkpointer,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decision", choices=["approve", "edit", "reject"], default="approve"
    )
    parser.add_argument("--gl", default="6500", help="Edited GL account (for --decision edit).")
    parser.add_argument("--approver", default="alice@controller")
    parser.add_argument(
        "--live", action="store_true", help="Real Bedrock coding + Titan retrieval (needs AWS)."
    )
    parser.add_argument(
        "--mcp", action="store_true", help="Route the ERP through the real MCP server."
    )
    args = parser.parse_args(argv)

    pack = load_policy_pack(POLICY_PACKS / "retail_non_po.yaml")
    deps = _build_deps(pack, live=args.live, mcp=args.mcp)
    sup = Supervisor(deps)

    mode = "LIVE Bedrock+Titan" if args.live else "scripted (offline)"
    erp_mode = "MCP server" if args.mcp else "in-process"
    print("=" * 78)
    print(f"  HITL: a $5,000 non-PO invoice (over the $1,000 ceiling)  [{mode} | ERP: {erp_mode}]")
    print("=" * 78)

    state = new_run_state(
        tenant_id="retail-demo", invoice_id="demo-hitl", document_id="demo-doc", invoice=_invoice()
    )
    paused = sup.run(state)

    print(f"\n1. Run paused for review: {paused.pending_hitl}")
    print(f"   proposed route : {paused.route.value if paused.route else None}")
    print(f"   proposed coding: {paused.gl_coding.gl_account if paused.gl_coding else None}")
    print("   (the approver has been notified; the run is halted awaiting a human)")

    decision = ReviewDecision(
        decision=ReviewDecisionType(args.decision),
        approver_identity=None if args.decision == "reject" else args.approver,
        edited_gl_account=args.gl if args.decision == "edit" else None,
        note=None if args.decision != "reject" else "Not a valid subscription.",
    )
    # Reject still needs an actor for the audit trail; supply one.
    if args.decision == "reject":
        decision = ReviewDecision(
            decision=ReviewDecisionType.REJECT,
            approver_identity=args.approver,
            note="Not a valid subscription.",
        )

    print(f"\n2. Human decides: {args.decision.upper()} (by {args.approver})")
    done = sup.resume(paused.run_id, decision)

    print("\n3. Run resumed and completed:")
    if done.decision is not None:
        print(f"   route      : {done.decision.route.value}")
        print(f"   decided_by : {done.decision.decided_by.value} (the human, never the agent)")
        print(f"   approver   : {done.decision.approver_identity}")
        if done.decision.gl_coding is not None:
            print(f"   gl coding  : {done.decision.gl_coding.gl_account}")
        print(f"   rationale  : {done.decision.rationale}")
    print(f"   hitl status: {done.hitl_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
