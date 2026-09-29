"""Demo: the live check ledger + a run's event stream and KPI summary.

    uv run python scripts/observability_demo.py

The Slice 11 demo checkpoint (console form). It runs an invoice through the
supervisor with an `EventEmitter` attached and prints each `RunEvent` **as it is
emitted** — the check ledger appears check by check in evaluation order, then the
decision and cost close it out, exactly as the UI will render it. It then prints
the normalized wire shape of one event (what the SSE stream sends) so the
frontend contract is visible.

Runs in-process with scripted models — no AWS. With OTEL/Langfuse configured
(env vars), the same run also exports spans/traces; unconfigured, tracing is a
silent no-op and this still shows the full event stream.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "mock_erp"))

from ap_agent.agent.erp_client import InProcessErpClient  # noqa: E402
from ap_agent.agent.supervisor import Supervisor, SupervisorDeps, new_run_state  # noqa: E402
from ap_agent.core.canonical import Invoice, InvoiceLine  # noqa: E402
from ap_agent.core.policy_pack import load_policy_pack  # noqa: E402
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money  # noqa: E402
from ap_agent.observability.events import EventChannel, EventEmitter, RunEvent  # noqa: E402
from ap_agent.rag.coding import RawGLProposal, ScriptedCodingModel  # noqa: E402

POLICY_PACKS = REPO_ROOT / "backend" / "policy_packs"

_GLYPH = {"pass": "\u2713", "flag": "\u26a0", "fail": "\u2717", "skip": "\u2014"}


def _ex(v: Any) -> Extracted[Any]:
    return Extracted(value=v, confidence=0.99, method=ExtractionMethod.PARSED_STRUCTURE)


def _money(a: str) -> Money:
    return Money(amount=Decimal(a), currency="USD")


def _invoice() -> Invoice:
    return Invoice(
        invoice_id="demo-obs",
        tenant_id="retail-demo",
        document_id="demo-doc",
        invoice_number=_ex("INV-OBS"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex("Datamesh Analytics"),
        currency=_ex("USD"),
        subtotal=_ex(_money("500.00")),
        total_amount=_ex(_money("500.00")),
        po_reference=None,
        resolved_vendor_id="V-1001",
        lines=(
            InvoiceLine(
                line_number=1,
                description=_ex("Analytics platform annual subscription"),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(_money("500.00")),
                line_total=_ex(_money("500.00")),
            ),
        ),
    )


class _FakeRetriever:
    def retrieve(self, session: Any, **k: Any) -> list[Any]:
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
def _fs() -> Any:
    yield None


def _print_event(ev: RunEvent) -> None:
    """Render each event as it arrives — the live check ledger, then decision/cost."""
    if ev.channel is EventChannel.CHECK:
        p = ev.payload
        glyph = _GLYPH.get(p.get("verdict", ""), "?")
        name = str(p.get("name", "")).ljust(22, ".")
        print(f"    [{ev.seq:2}] {glyph} {name} {p.get('verdict', '').upper():5} {p.get('reasoning', '')[:70]}")
    elif ev.channel is EventChannel.DECISION:
        p = ev.payload
        print(f"    [{ev.seq:2}] DECISION  route={p['route']} by={p['actor']} gl={p.get('gl_account')}")
    elif ev.channel is EventChannel.COST:
        p = ev.payload
        print(f"    [{ev.seq:2}] COST      in={p['input_tokens']} out={p['output_tokens']} usd={p['usd']}")
    else:
        print(f"    [{ev.seq:2}] {ev.channel.value.upper()} {ev.payload}")


def main() -> int:
    pack = load_policy_pack(POLICY_PACKS / "retail_non_po.yaml")
    state = new_run_state(
        tenant_id="retail-demo", invoice_id="demo-obs", document_id="demo-doc", invoice=_invoice()
    )
    # The sink prints each event live as the run emits it.
    emitter = EventEmitter(run_id=state.run_id, trace_id="demo-trace", sink=_print_event)
    deps = SupervisorDeps(
        tenant_id="retail-demo",
        policy=pack,
        erp=InProcessErpClient(),
        retriever=_FakeRetriever(),
        coding_model=ScriptedCodingModel(
            RawGLProposal(
                gl_account="7200",
                confidence=0.95,
                citation_refs=("policy/gl_coding_policy_software_subscriptions.md#1",),
            )
        ),
        session_factory=_fs,
        emitter=emitter,
    )

    print("=" * 78)
    print("  Live RunEvent stream (check ledger appears check by check, in order)")
    print("=" * 78)
    out = Supervisor(deps).run(state)

    check_events = [e for e in emitter.events if e.channel is EventChannel.CHECK]
    print(
        f"\n  route={out.route.value if out.route else None} | "
        f"{len(check_events)} check events (one per check) | "
        f"{len(emitter.events)} events total, seq 0..{emitter.events[-1].seq}"
    )

    print("\n  One event on the wire (the SSE/WS contract the frontend reads):")
    print("  " + json.dumps(emitter.events[0].as_wire(), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
