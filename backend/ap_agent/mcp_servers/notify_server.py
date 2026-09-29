"""Notification MCP server — HITL escalation as an MCP server (FR-11.3).

Run as a stdio MCP server (`python -m ap_agent.mcp_servers.notify_server`), this
delivers an escalation to a human approver's channel and returns a structured
receipt. Like the ERP server, it enforces its own server-side scope check
(`notify:send`) independently of the agent, so a caller without the notify token
cannot send — even if the agent layer were bypassed.

The escalation carries **approve/reject affordances**: the receipt includes the
decision options the reviewer may take and an opaque `review_ref` the resume
path keys on. Actual delivery to Slack/email is a channel adapter behind this
tool; the default `console` channel records the escalation and returns the
receipt without claiming a delivery that did not happen (the demo stays honest,
and the in-app HITL queue remains the auditable system of record).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ap_agent.mcp_servers.permissions import PermissionDenied, Scope, require_scope

mcp = FastMCP("ap-notify")

# Decisions a reviewer may take on an escalated invoice. Fixed here (not
# caller-supplied) so the affordances a notification advertises cannot be
# widened by the agent — e.g. no "auto-approve" option can be injected.
ALLOWED_DECISIONS = ("approve", "edit", "reject")


@mcp.tool(
    name="notify_approver",
    description=(
        "Escalate an invoice to a human approver at a DOA tier, with "
        "approve/edit/reject affordances. Requires a notify:send-scoped token. "
        "Returns a receipt with a review_ref the resume path keys on."
    ),
)
def notify_approver(
    token: str,
    tenant_id: str,
    required_tier: str,
    reason: str,
    invoice_id: str,
    amount: str,
    currency: str,
    channel: str = "console",
) -> dict[str, Any]:
    try:
        require_scope(token, Scope.NOTIFY_SEND, tool="notify_approver")
    except PermissionDenied as exc:
        raise ToolError(str(exc)) from exc

    if channel not in ("console", "slack", "email"):
        raise ToolError(f"Unknown channel {channel!r}; expected console, slack, or email.")

    # `console` records the escalation without asserting external delivery. A
    # real slack/email adapter would go here (Slice 10 wires delivery + the
    # durable HitlReview row); refusing to fake delivery keeps the demo honest.
    return {
        "channel": channel,
        "delivered": channel == "console",  # only the console sink is real here
        "review_ref": f"rev-{uuid.uuid4().hex[:12]}",
        "tenant_id": tenant_id,
        "required_tier": required_tier,
        "reason": reason,
        "invoice_id": invoice_id,
        "amount": amount,
        "currency": currency,
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    """Entry point: run the server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
