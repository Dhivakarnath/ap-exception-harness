"""Demo: run whole invoices through the Slice 8 supervisor, end to end.

    uv run python scripts/supervisor_demo.py --all          # one per scenario
    uv run python scripts/supervisor_demo.py auto_approve    # one scenario

This is the Slice 8 demo checkpoint: the full pipeline — deterministic route,
policy engine, GL coding, terminal decision — composed over the mock ERP, in
process. It runs with **scripted models by default** (no AWS, no network): a
scripted coding model and a fake retriever stand in for the RAG step so the
*harness* is what is being demonstrated, not the LLM. Slice 13's DeepEval
harness grades the live models end to end.

Each scenario prints the branch taken (PO vs non-PO — a pure property, never a
model choice), the deterministic check ledger, and the terminal decision with
its route, actor, and any GL coding. The point is that the control flow and the
money-affecting decision are deterministic and inspectable, with the model
confined to the two reading/judgement steps.
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
from ap_agent.agent.supervisor import Supervisor, SupervisorDeps, new_run_state  # noqa: E402
from ap_agent.core.canonical import Invoice, InvoiceLine  # noqa: E402
from ap_agent.core.policy_pack import load_policy_pack  # noqa: E402
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money  # noqa: E402
from ap_agent.rag.coding import RawGLProposal, ScriptedCodingModel  # noqa: E402

POLICY_PACKS = REPO_ROOT / "backend" / "policy_packs"


def _ex(value: Any, confidence: float = 0.99) -> Extracted[Any]:
    return Extracted(value=value, confidence=confidence, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="USD")


def _line(num: int, desc: str, qty: str, unit: str) -> InvoiceLine:
    q = Decimal(qty)
    u = _money(unit)
    return InvoiceLine(
        line_number=num,
        description=_ex(desc),
        quantity=_ex(q),
        unit_price=_ex(u),
        line_total=_ex(Money(amount=q * u.amount, currency="USD")),
    )


def _invoice(
    *,
    total: str,
    po: str | None,
    description: str,
    vendor: str = "Datamesh Analytics",
    vendor_id: str = "V-1001",
    lines: tuple[InvoiceLine, ...] | None = None,
) -> Invoice:
    return Invoice(
        invoice_id="demo-inv",
        tenant_id="retail-demo",
        document_id="demo-doc",
        invoice_number=_ex("INV-DEMO"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex(vendor),
        currency=_ex("USD"),
        subtotal=_ex(_money(total)),
        total_amount=_ex(_money(total)),
        po_reference=_ex(po) if po else None,
        resolved_vendor_id=vendor_id,
        lines=lines
        or (
            InvoiceLine(
                line_number=1,
                description=_ex(description),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(_money(total)),
                line_total=_ex(_money(total)),
            ),
        ),
    )


class _FakeRetriever:
    def __init__(self, grounded: bool) -> None:
        self._grounded = grounded

    def retrieve(self, session: Any, **kwargs: Any) -> list[Any]:
        if not self._grounded:
            return []
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


# scenario -> (invoice, grounded_retrieval)
def _scenarios() -> dict[str, tuple[Invoice, bool]]:
    return {
        "auto_approve": (
            _invoice(total="500.00", po=None, description="Analytics platform annual subscription"),
            True,
        ),
        "route_for_approval_amount": (
            _invoice(total="5000.00", po=None, description="Analytics platform annual subscription"),
            True,
        ),
        "route_for_approval_ungrounded": (
            _invoice(total="500.00", po=None, description="Payroll tax remittance to the revenue authority"),
            False,
        ),
        # Lines match seeded PO-2001 exactly, so the three-way match passes; the
        # 2400 total is above the 1000 touchless ceiling, so a *clean* PO-backed
        # invoice still routes for approval — the correct, realistic outcome.
        "po_backed": (
            _invoice(
                total="2400.00",
                po="PO-2001",
                description="Steel brackets and inbound freight",
                lines=(
                    _line(1, "Steel bracket, 40mm", "120", "12.00"),
                    _line(2, "Inbound freight", "1", "960.00"),
                ),
            ),
            True,
        ),
    }


def _scripted_deps(pack: Any, grounded: bool, *, mcp: bool = False) -> SupervisorDeps:
    """Offline coding/retrieval (scripted + fake, no AWS); ERP is in-process
    unless ``mcp`` is set, in which case it goes over the real MCP server."""
    coding = ScriptedCodingModel(
        RawGLProposal(
            gl_account="7200",
            confidence=0.95,
            citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",),
            reasoning="SaaS subscription.",
        )
    )
    return SupervisorDeps(
        tenant_id="retail-demo",
        policy=pack,
        erp=_erp_client(mcp),
        retriever=cast("Any", _FakeRetriever(grounded)),  # duck-typed stand-in
        coding_model=coding,
        session_factory=_fake_session,
    )


def _erp_client(mcp: bool) -> Any:
    """The ERP client for the demo: in-process by default, or the real MCP
    stdio server when ``--mcp`` is set (server-side permission enforcement,
    same ErpClient Protocol)."""
    if mcp:
        from ap_agent.mcp_servers.erp_mcp_client import McpErpClient

        return McpErpClient()  # erp-writer purpose token; the pipeline reads and posts
    return InProcessErpClient()


def _live_deps(pack: Any, *, mcp: bool = False) -> SupervisorDeps:
    """Live dependencies: real Bedrock Nova Lite coding + Titan hybrid retriever
    against the real Postgres corpus. Makes real (cheap) AWS calls. With
    ``mcp=True`` the ERP also goes over the real MCP stdio server — the full
    stack (live inference + real MCP transport) in one run.

    The retriever needs the corpus ingested with Titan (``make rag-ingest``).
    """
    from ap_agent.persistence.db import session_scope
    from ap_agent.rag.coding import BedrockCodingModel
    from ap_agent.rag.retriever import HybridRetriever

    return SupervisorDeps(
        tenant_id="retail-demo",
        policy=pack,
        erp=_erp_client(mcp),
        retriever=HybridRetriever(),  # Titan embeddings, real pgvector + tsvector
        coding_model=BedrockCodingModel(),  # Nova Lite via with_structured_output
        session_factory=session_scope,
    )


def _run_scenario(
    name: str, invoice: Invoice, grounded: bool, *, live: bool = False, mcp: bool = False
) -> None:
    pack = load_policy_pack(POLICY_PACKS / "retail_non_po.yaml")
    deps = _live_deps(pack, mcp=mcp) if live else _scripted_deps(pack, grounded, mcp=mcp)
    state = new_run_state(
        tenant_id="retail-demo", invoice_id=invoice.invoice_id, document_id="demo-doc", invoice=invoice
    )
    out = Supervisor(deps).run(state)

    mode = "LIVE Bedrock+Titan" if live else "scripted (offline)"
    erp_mode = "MCP server" if mcp else "in-process"
    print("=" * 78)
    print(f"  scenario   : {name}  [{mode} | ERP: {erp_mode}]")
    print(f"  invoice    : {invoice.vendor_name.value} | {invoice.lines[0].description.value}")
    print(f"  total      : {invoice.total_amount.value}")
    print(f"  branch     : {'non-PO' if out.is_non_po else 'PO-backed'} (from Invoice.is_non_po)")
    print("=" * 78)

    if out.error is not None:
        print(f"  RUN FAILED (fail-loud): {out.error.get('error_type')}: {out.error.get('message')}")
        print()
        return

    if out.ledger.results:
        print("  check ledger:")
        for line in out.ledger.render().splitlines():
            print(f"    {line}")
    if out.coding_result is not None:
        print(f"  coding     : {out.coding_result.outcome.value}")
    decision = out.decision
    if decision is not None:
        print(f"  route      : {decision.route.value}")
        print(f"  decided_by : {decision.decided_by.value}")
        if decision.required_approver_tier:
            print(f"  approver   : tier {decision.required_approver_tier!r} (a human, never the agent)")
        if out.gl_coding is not None:
            src = "inherited from PO" if out.gl_coding.inherited_from_po else "grounded auto-code"
            print(f"  gl coding  : {out.gl_coding.gl_account} ({src})")
            if out.gl_coding.citations:
                print(f"  citations  : {', '.join(out.gl_coding.citations)}")
        print(f"  rationale  : {decision.rationale}")
    print(f"  context util: {out.context_utilisation}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", help="A single scenario to run.")
    parser.add_argument("--all", action="store_true", help="Run one of every scenario.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use real Bedrock Nova Lite coding + Titan hybrid retrieval "
        "(needs AWS creds, Postgres, and an ingested corpus). Makes real calls.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Route the ERP through the real MCP stdio server (server-side "
        "permission enforcement). Combine with --live for the full stack.",
    )
    args = parser.parse_args(argv)

    scenarios = _scenarios()
    if args.all or args.scenario is None:
        for name, (invoice, grounded) in scenarios.items():
            _run_scenario(name, invoice, grounded, live=args.live, mcp=args.mcp)
        return 0

    if args.scenario not in scenarios:
        print(f"Unknown scenario {args.scenario!r}. Choices: {', '.join(scenarios)}")
        return 2
    invoice, grounded = scenarios[args.scenario]
    _run_scenario(args.scenario, invoice, grounded, live=args.live, mcp=args.mcp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
