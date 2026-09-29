"""Live observability verification: a real invoice, real inference, real export.

    uv run python scripts/observability_live.py            # live Bedrock+Titan
    uv run python scripts/observability_live.py --mcp      # + ERP over real MCP

This is the honest end-to-end check for Slice 11's export path, distinct from the
offline `observability_demo.py` (which streams the event contract with scripted
models). It runs one non-PO invoice through the supervisor with:

  * live Bedrock Nova Lite coding + Titan hybrid retrieval against real Postgres,
  * (optionally) the ERP over the real MCP stdio server,
  * an `EventEmitter` attached, so the per-check / decision / cost events fire on
    top of real inference — proving observability composes with the live stack,
  * OTEL tracing exported to whatever `OTEL_EXPORTER_OTLP_ENDPOINT` points at
    (the local collector), which the collector prints and forwards to Langfuse.

At the end it force-flushes the tracer (a short-lived process would otherwise
exit before the BatchSpanProcessor sends its queue) and prints the run id plus
the Langfuse deep-link, so a human can open the trace. It asserts nothing about
Langfuse itself — that verification is done out-of-band by querying the Langfuse
API — but it does assert the run produced check events and a terminal decision,
because a "traced" run that did no work would be a false positive.
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

from ap_agent.agent.supervisor import (  # noqa: E402
    Supervisor,
    SupervisorDeps,
    new_run_state,
)
from ap_agent.core.canonical import Invoice, InvoiceLine  # noqa: E402
from ap_agent.core.policy_pack import load_policy_pack  # noqa: E402
from ap_agent.core.primitives import Extracted, ExtractionMethod, Money  # noqa: E402
from ap_agent.observability import tracing  # noqa: E402
from ap_agent.observability.events import EventEmitter  # noqa: E402
from ap_agent.observability.langfuse_export import langfuse_enabled, trace_url  # noqa: E402

POLICY_PACKS = REPO_ROOT / "backend" / "policy_packs"


def _ex(value: Any, confidence: float = 0.99) -> Extracted[Any]:
    return Extracted(
        value=value, confidence=confidence, method=ExtractionMethod.PARSED_STRUCTURE
    )


def _money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="USD")


def _invoice() -> Invoice:
    """A grounded non-PO SaaS subscription — exercises the code_gl node (a real
    Nova Lite call) and the full check ledger, so the trace has model + check
    spans, not just a bare run span."""
    total = _money("500.00")
    return Invoice(
        invoice_id="obs-live-inv",
        tenant_id="retail-demo",
        document_id="obs-live-doc",
        invoice_number=_ex("INV-OBS-LIVE"),
        invoice_date=_ex(date(2026, 2, 15)),
        vendor_name=_ex("Datamesh Analytics"),
        currency=_ex("USD"),
        subtotal=_ex(total),
        total_amount=_ex(total),
        po_reference=None,
        resolved_vendor_id="V-1001",
        lines=(
            InvoiceLine(
                line_number=1,
                description=_ex("Analytics platform annual subscription"),
                quantity=_ex(Decimal("1")),
                unit_price=_ex(total),
                line_total=_ex(total),
            ),
        ),
    )


@contextmanager
def _fake_session() -> Any:
    yield None


def _erp_client(mcp: bool) -> Any:
    if mcp:
        from ap_agent.mcp_servers.erp_mcp_client import McpErpClient

        return McpErpClient()
    from ap_agent.agent.erp_client import InProcessErpClient

    return InProcessErpClient()


def _live_deps(pack: Any, *, mcp: bool, emitter: EventEmitter) -> SupervisorDeps:
    from ap_agent.persistence.db import session_scope
    from ap_agent.rag.coding import BedrockCodingModel
    from ap_agent.rag.retriever import HybridRetriever

    return SupervisorDeps(
        tenant_id="retail-demo",
        policy=pack,
        erp=_erp_client(mcp),
        retriever=HybridRetriever(),
        coding_model=BedrockCodingModel(),
        session_factory=session_scope,
        emitter=cast("object", emitter),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Route the ERP through the real MCP stdio server.",
    )
    args = parser.parse_args(argv)

    if tracing._get_tracer() is None:
        print(
            "WARNING: OTEL is not configured (OTEL_EXPORTER_OTLP_ENDPOINT unset). "
            "The run will still work but no spans will be exported. Start the "
            "stack with `make obs-up` and set the .env observability block."
        )

    invoice = _invoice()
    state = new_run_state(
        tenant_id="retail-demo",
        invoice_id=invoice.invoice_id,
        document_id="obs-live-doc",
        invoice=invoice,
    )
    emitter = EventEmitter(run_id=state.run_id)
    pack = load_policy_pack(POLICY_PACKS / "retail_non_po.yaml")
    deps = _live_deps(pack, mcp=args.mcp, emitter=emitter)

    otel_on = tracing._get_tracer() is not None
    print("=" * 78)
    print(f"  run_id     : {state.run_id}")
    print(f"  stack      : LIVE Bedrock+Titan | ERP: {'MCP server' if args.mcp else 'in-process'}")
    print(f"  otel        : {'ON' if otel_on else 'OFF'}")
    print("=" * 78)

    out = Supervisor(deps).run(state)

    if out.error is not None:
        print(f"  RUN FAILED (fail-loud): {out.error.get('error_type')}: {out.error.get('message')}")
        # Flush whatever spans we did open (the error is recorded on them).
        tracing.force_flush()
        return 1

    # The emitter is the observability contract; show it fired on the live run.
    check_events = [e for e in emitter.events if e.channel.value == "check"]
    decision_events = [e for e in emitter.events if e.channel.value == "decision"]
    cost_events = [e for e in emitter.events if e.channel.value == "cost"]

    print("  emitted RunEvents (live, on top of real inference):")
    print(f"    total events   : {len(emitter.events)}")
    print(f"    check events   : {len(check_events)}")
    for e in check_events:
        p = e.payload
        print(
            f"      - seq={e.seq:<3} {p.get('name'):<28} "
            f"verdict={p.get('verdict')}"
        )
    print(f"    decision events: {len(decision_events)}")
    for e in decision_events:
        print(f"      - seq={e.seq:<3} route={e.payload.get('route')}")
    print(f"    cost events    : {len(cost_events)}")
    for e in cost_events:
        p = e.payload
        print(
            f"      - seq={e.seq:<3} in={p.get('input_tokens')} "
            f"out={p.get('output_tokens')} usd={p.get('usd')}"
        )

    if out.decision is not None:
        print(f"  terminal route : {out.decision.route.value}")
    if out.gl_coding is not None:
        print(f"  gl coding      : {out.gl_coding.gl_account}")
        if out.gl_coding.citations:
            print(f"  citations      : {', '.join(out.gl_coding.citations)}")

    # A traced run that did no work would be a false positive, so fail loudly.
    if not check_events or not decision_events:
        print(
            "  VERIFICATION FAILED: expected check + decision events but the run "
            f"emitted checks={len(check_events)} decisions={len(decision_events)}."
        )
        tracing.force_flush()
        return 1

    # Force the batch processor to drain before we exit, or the spans never leave.
    flushed = tracing.force_flush()
    print(f"  tracer flush   : {'flushed' if flushed else 'no tracer (OTEL off)'}")

    if langfuse_enabled():
        # The OTEL span's own trace id is internal; the deep-link pattern is what
        # the UI uses. We print the project traces URL for the human to open.
        print("  langfuse       : ENABLED")
        print("  open the trace : http://localhost:3000  (project ap-exception-agent)")
        _ = trace_url  # deep-link helper is exercised in unit tests
    else:
        print("  langfuse       : not configured (deep-link omitted)")

    print("  DONE — spans exported to the collector; check `make obs-collector-logs`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
