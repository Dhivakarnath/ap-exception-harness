"""Demo: defence in depth — disable Layer 1, show Layer 2 still refuses.

    uv run python scripts/mcp_defense_demo.py

The Slice 9 demo checkpoint. The permission model has independent layers:

  * Layer 1 — the agent's RBAC middleware never offers a write tool to a
    read-only sub-agent (Slice 8, in the agent process);
  * Layer 2 — the ERP MCP server's own scope check refuses a write from a
    read-only-token client (Slice 9, in the server process);
  * Layer 3 — the ERP ledger itself refuses on business grounds (blocked
    vendor, control-account coding), regardless of the above.

This script demonstrates Layer 2 in isolation by **bypassing Layer 1 entirely**:
it talks straight to the MCP server with a bare client — no agent, no RBAC
middleware — first with a read-only token (refused) and then with a write token
(allowed). If Layer 2 were only cosmetic, the read-only write would go through;
it does not, which is the whole point of defence in depth.

Requires nothing but the mock ERP code on the path (the server spawns as a
stdio subprocess). No AWS, no Postgres.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "mock_erp"))

from ap_agent.core.primitives import Money  # noqa: E402
from ap_agent.errors import PolicyViolationError  # noqa: E402
from ap_agent.mcp_servers.erp_mcp_client import McpErpClient  # noqa: E402


def _attempt_write(client: McpErpClient, label: str) -> None:
    print(f"  [{label}] attempting post_bill (Layer 1 / agent RBAC is NOT in the picture) ...")
    try:
        result = client.post_bill(
            tenant_id="retail-demo",
            idempotency_key=f"defense-demo-{label}",
            doc_number=f"INV-DEF-{label}",
            vendor_id="V-1001",
            txn_date=date(2026, 2, 15),
            total_amount=Money(amount=Decimal("500.00"), currency="USD"),
            gl_account="7200",
            po_doc_number=None,
            private_note="defence-in-depth demo",
        )
        print(f"  [{label}] ALLOWED — posted bill {result['bill']['DocNumber']}")
    except PolicyViolationError as exc:
        print(f"  [{label}] REFUSED at the MCP server (Layer 2): {exc}")


def main() -> int:
    print("=" * 78)
    print("  Defence in depth: Layer 1 (agent RBAC) bypassed; only Layer 2 (MCP) acts")
    print("=" * 78)

    print("\n1. Read-only purpose token ('erp-reader'):")
    reader = McpErpClient(token="erp-reader")  # noqa: S106 - purpose-scope id, not a secret
    print(f"  [reader] read PO-2001 -> {reader.get_purchase_order('PO-2001').po_number} (reads are allowed)")
    _attempt_write(reader, "reader")
    reader.close()

    print("\n2. Write purpose token ('erp-writer'):")
    writer = McpErpClient(token="erp-writer")  # noqa: S106 - purpose-scope id, not a secret
    _attempt_write(writer, "writer")
    writer.close()

    print("\nConclusion: the read-only token's write was refused by the server itself,")
    print("with no agent-layer control involved — that is the defence-in-depth property.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
