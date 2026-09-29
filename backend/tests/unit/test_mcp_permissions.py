"""Slice 9 — the three-layer permission model, server side (unit tier).

The load-bearing claim of this slice is **defence in depth**: the same forbidden
action is refused independently at more than one layer, so bypassing one does
not open the door. Slice 8 proved Layer 1 (the agent's RBAC middleware removes a
tool the sub-agent may not use). These tests prove Layer 2 — the MCP server's
own scope check — refuses the same action **with the agent layer entirely out of
the picture**: they call the server tool functions directly, no agent, no
middleware, and show the write is still refused with a read-only token.

They also assert the properties that make the control trustworthy:

* malformed arguments are rejected (input validation is server-side too);
* an unknown/absent token grants nothing (fail closed);
* the permission decision is *code*, not prompt text — a static check that the
  scope guard is a real call in every write tool, so no permission rests on a
  tool description a model could ignore.

No stdio subprocess is needed here: the scope check lives in the tool body, so
calling the function directly exercises the exact control an MCP client would
hit. A separate integration test drives the real stdio transport end to end.
"""

from __future__ import annotations

import inspect

import pytest

from ap_agent.mcp_servers import erp_server, notify_server
from ap_agent.mcp_servers.permissions import (
    Scope,
    granted_scopes,
    require_scope,
)

pytest.importorskip("mcp")

from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

# --------------------------------------------------------------- scope model


class TestScopeModel:
    def test_reader_token_grants_only_read(self) -> None:
        assert granted_scopes("erp-reader") == frozenset({Scope.ERP_READ})

    def test_writer_token_grants_read_and_write(self) -> None:
        assert granted_scopes("erp-writer") == frozenset({Scope.ERP_READ, Scope.ERP_WRITE})

    def test_unknown_token_grants_nothing(self) -> None:
        # Fail closed: an unknown or absent token is powerless.
        assert granted_scopes("bogus") == frozenset()
        assert granted_scopes(None) == frozenset()

    def test_no_payment_scope_exists(self) -> None:
        # The system terminates at "approved for payment"; there is no scope,
        # and therefore no grantable capability, that moves money.
        names = {s.value for s in Scope}
        assert not any("pay" in n or "disburse" in n or "remit" in n for n in names)

    def test_require_scope_refuses_without_grant(self) -> None:
        from ap_agent.mcp_servers.permissions import PermissionDenied

        with pytest.raises(PermissionDenied):
            require_scope("erp-reader", Scope.ERP_WRITE, tool="post_bill")


# --------------------------------------------- Layer 2 refuses, agent bypassed


class TestServerSideRefusalWithAgentBypassed:
    """Call the ERP server tools directly — no agent, no RBAC middleware — and
    confirm the server's own scope check still refuses a write with a read
    token. This is the defence-in-depth property: Layer 1 is not even present
    here, and the write is refused anyway."""

    def test_read_token_cannot_post_a_bill(self) -> None:
        with pytest.raises(ToolError, match="erp:write"):
            erp_server.post_bill(
                "erp-reader",
                "idem-refuse-1",
                "INV-X",
                "V-1001",
                "2026-02-15",
                "500.00",
                "USD",
                "should be refused",
                gl_account="7200",
            )

    def test_read_token_cannot_raise_an_exception(self) -> None:
        with pytest.raises(ToolError, match="erp:write"):
            erp_server.raise_exception(
                "erp-reader",
                "idem-refuse-2",
                "DataQuality",
                "detail",
                "agent",
            )

    def test_unknown_token_cannot_read(self) -> None:
        with pytest.raises(ToolError, match="erp:read"):
            erp_server.get_purchase_order("bogus-token", "PO-2001")

    def test_notify_requires_its_own_scope(self) -> None:
        with pytest.raises(ToolError, match="notify:send"):
            notify_server.notify_approver(
                "erp-reader",  # wrong purpose token: has no notify scope
                "retail-demo",
                "controller",
                "reason",
                "inv-1",
                "500.00",
                "USD",
            )


# --------------------------------------------------------- input validation


class TestServerSideInputValidation:
    def test_bad_date_is_rejected(self) -> None:
        with pytest.raises(ToolError, match="ISO date"):
            erp_server.list_historical_bills("erp-reader", "V-1001", "not-a-date")

    def test_bad_amount_on_post_is_rejected(self) -> None:
        # Writer token (passes the scope gate) but a malformed amount — the
        # server validates the payload, not just the permission.
        with pytest.raises(ToolError, match="Invalid amount"):
            erp_server.post_bill(
                "erp-writer",
                "idem-badamt",
                "INV-X",
                "V-1001",
                "2026-02-15",
                "not-a-number",
                "USD",
                "rationale",
            )

    def test_notify_rejects_unknown_channel(self) -> None:
        with pytest.raises(ToolError, match="channel"):
            notify_server.notify_approver(
                "notifier",
                "retail-demo",
                "controller",
                "reason",
                "inv-1",
                "500.00",
                "USD",
                channel="carrier-pigeon",
            )


# ---------------------------------------- no permission rests on prompt text


class TestPermissionIsCodeNotPrompt:
    """A static guarantee: every write/read tool actually *calls* the scope
    guard in its body. A permission that lived only in a tool's description
    (which a model can ignore) would be no permission at all."""

    def _tool_source(self, fn: object) -> str:
        return inspect.getsource(fn)  # type: ignore[arg-type]

    def test_every_erp_write_tool_calls_the_scope_guard(self) -> None:
        for tool in (erp_server.post_bill, erp_server.raise_exception):
            src = self._tool_source(tool)
            assert "_guard(" in src and "ERP_WRITE" in src, (
                f"{getattr(tool, '__name__', tool)} must enforce erp:write in code, "
                "not in its description"
            )

    def test_every_erp_read_tool_calls_the_scope_guard(self) -> None:
        for tool in (
            erp_server.get_purchase_order,
            erp_server.get_goods_receipt,
            erp_server.get_vendor,
            erp_server.list_historical_bills,
        ):
            src = self._tool_source(tool)
            assert "_guard(" in src and "ERP_READ" in src

    def test_notify_tool_calls_the_scope_guard(self) -> None:
        src = self._tool_source(notify_server.notify_approver)
        assert "require_scope(" in src and "NOTIFY_SEND" in src

    def test_erp_server_exposes_no_payment_tool(self) -> None:
        # The server's tool surface must contain nothing that moves money.
        tool_names = {
            name
            for name, obj in vars(erp_server).items()
            if callable(obj) and not name.startswith("_")
        }
        assert not any(
            frag in n for n in tool_names for frag in ("pay", "disburse", "remit", "settle")
        )
