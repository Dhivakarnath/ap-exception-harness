"""Server-side permission scopes for the MCP layer (FR-9.2, FR-9.4).

This is the *second* layer of defence in depth. The agent already filters its
own toolset (Slice 8 RBAC middleware) and gates writes through the deterministic
policy engine — but that is all inside the agent process, and an attacker who
compromised the agent, or a bug that bypassed the middleware, would sail past
it. So the MCP servers enforce their own permission checks **independently, on
the server side**, keyed to a purpose-scoped token the caller must present.

The design is deliberately boring and mechanical:

* Each tool declares the single scope it requires (``erp:read``, ``erp:write``,
  ``notify:send``). A tool with no scope is unreachable — fail closed.
* A caller presents a token that maps to a set of granted scopes. The server
  checks the required scope against the granted set **before running the tool**,
  and raises if it is missing. This check is code, not prompt text — a model
  cannot talk its way past a Python ``if``.
* Tokens are *purpose-scoped and least-privilege*: the read token grants only
  ``erp:read``; the write token grants read+write; there is no "admin" token and
  no token that grants payment (there is no payment scope at all). The caller's
  own credentials are never propagated — the agent presents the server's
  purpose token, not a user identity (FR-9.3).

The key property a test asserts: give the ERP server a read-only token and call
a write tool, and it refuses — *even with the agent's Layer-1 RBAC removed* —
because this check lives on the server, not in the agent.
"""

from __future__ import annotations

from enum import StrEnum


class Scope(StrEnum):
    """The complete set of permission scopes across the MCP servers.

    There is deliberately no payment scope: the system terminates at "approved
    for payment" and no server-side capability to move money exists (ADR-011).
    """

    ERP_READ = "erp:read"
    """Read PO/GRN/vendor/bill history."""

    ERP_WRITE = "erp:write"
    """Post a bill, raise an exception. Implies read is *not* automatic — a
    write token grants both explicitly (see `TOKEN_SCOPES`)."""

    NOTIFY_SEND = "notify:send"
    """Send an escalation notification."""


# Purpose-scoped, least-privilege tokens. In a real deployment these are
# short-lived secrets minted per purpose by a broker; here they are stable
# identifiers whose *scope grant* is the part that matters for the control.
# Names describe the purpose, not a user — the agent presents a purpose token,
# never a caller's identity (FR-9.3).
TOKEN_SCOPES: dict[str, frozenset[Scope]] = {
    "erp-reader": frozenset({Scope.ERP_READ}),
    "erp-writer": frozenset({Scope.ERP_READ, Scope.ERP_WRITE}),
    "notifier": frozenset({Scope.NOTIFY_SEND}),
}


class PermissionDenied(Exception):
    """A server-side scope check refused a call. Raised inside a tool so it
    surfaces to the MCP client as a tool error, independently of any agent-layer
    control."""


def granted_scopes(token: str | None) -> frozenset[Scope]:
    """Resolve a token to its granted scopes. An unknown/missing token grants
    nothing — fail closed."""
    if token is None:
        return frozenset()
    return TOKEN_SCOPES.get(token, frozenset())


def require_scope(token: str | None, required: Scope, *, tool: str) -> None:
    """Enforce that ``token`` grants ``required`` for ``tool``, or refuse.

    The load-bearing check. Called at the top of every tool body, before any
    work — so a caller lacking the scope never reaches the ledger, regardless of
    what the agent layer did or did not do.
    """
    scopes = granted_scopes(token)
    if required not in scopes:
        raise PermissionDenied(
            f"Tool {tool!r} requires scope {required.value!r}; the presented "
            f"token grants {sorted(s.value for s in scopes) or 'no scopes'}. "
            "Refused server-side."
        )
