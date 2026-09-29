"""Slice 9 — the ERP MCP transport end to end over real stdio.

Marked `integration` because it spawns the ERP MCP server as a stdio subprocess
(`python -m ap_agent.mcp_servers.erp_server`) and drives it through the real
`McpErpClient` + langchain-mcp-adapters transport. The unit tests
(`test_mcp_permissions`) prove the server-side control by calling the tool
functions directly; this test proves the *same* control holds across the actual
MCP wire, and that the client deserialises canonical models correctly.

The defence-in-depth assertion here is the strongest form: a read-only-token
client, going over the real transport, is refused when it attempts a write —
the refusal originates at the server process, not in the client.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ap_agent.core.canonical import VendorStatus
from ap_agent.core.primitives import Money
from ap_agent.errors import PolicyViolationError

pytestmark = pytest.mark.integration


# Module-scoped clients: each spawns a stdio subprocess + background loop, so
# they are created once and reused across the module's tests rather than per
# test. This mirrors real usage (one client per process) and avoids spawning a
# fresh subprocess for every assertion.
@pytest.fixture(scope="module")
def writer_client():  # noqa: ANN201 - fixture
    from ap_agent.mcp_servers.erp_mcp_client import McpErpClient

    client = McpErpClient(token="erp-writer")
    yield client
    client.close()


@pytest.fixture(scope="module")
def reader_client():  # noqa: ANN201 - fixture
    from ap_agent.mcp_servers.erp_mcp_client import McpErpClient

    client = McpErpClient(token="erp-reader")
    yield client
    client.close()


class TestMcpReads:
    def test_reads_a_purchase_order_over_the_wire(self, writer_client) -> None:  # noqa: ANN001
        po = writer_client.get_purchase_order("PO-2001")
        assert po is not None
        assert po.po_number == "PO-2001"
        # The account-id -> account-number mapping happened server-side.
        assert po.gl_account in {"5000", "5100", "6410", "6420", "6500", "6600", "7200"}
        assert len(po.lines) >= 1

    def test_missing_po_returns_none(self, writer_client) -> None:  # noqa: ANN001
        assert writer_client.get_purchase_order("PO-DOES-NOT-EXIST") is None

    def test_reads_a_vendor(self, writer_client) -> None:  # noqa: ANN001
        vendor = writer_client.get_vendor("V-1001")
        assert vendor is not None
        assert isinstance(vendor.status, VendorStatus)


class TestMcpWrites:
    def test_writer_can_post_a_bill(self, writer_client) -> None:  # noqa: ANN001
        result = writer_client.post_bill(
            tenant_id="retail-demo",
            idempotency_key="int-mcp-post-1",
            doc_number="INV-INT-1",
            vendor_id="V-1001",
            txn_date=date(2026, 2, 15),
            total_amount=Money(amount=Decimal("500.00"), currency="USD"),
            gl_account="7200",
            po_doc_number=None,
            private_note="integration test",
        )
        assert result["bill"]["DocNumber"] == "INV-INT-1"


class TestMcpDefenseInDepth:
    def test_reader_token_write_is_refused_at_the_server(self, reader_client) -> None:  # noqa: ANN001
        # The agent layer is entirely absent here — this is a bare client with a
        # read-only token going straight to the server. The write must still be
        # refused, because the scope check lives on the server.
        with pytest.raises(PolicyViolationError) as exc:
            reader_client.post_bill(
                tenant_id="retail-demo",
                idempotency_key="int-mcp-refuse-1",
                doc_number="INV-INT-2",
                vendor_id="V-1001",
                txn_date=date(2026, 2, 15),
                total_amount=Money(amount=Decimal("500.00"), currency="USD"),
                gl_account="7200",
                po_doc_number=None,
                private_note="should be refused server-side",
            )
        assert "erp:write" in str(exc.value)

    def test_reader_can_still_read(self, reader_client) -> None:  # noqa: ANN001
        # Least privilege, not no privilege: the reader token can read.
        assert reader_client.get_purchase_order("PO-2001") is not None
