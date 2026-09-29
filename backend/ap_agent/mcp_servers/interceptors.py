"""MCP tool-call interceptors: auth, structured logging, transient-only retry
(FR-8.1).

`MultiServerMCPClient` composes interceptors in an onion (first = outermost)
around every remote tool call. These three are the cross-cutting concerns the
MCP transport needs, and they mirror the agent-side middleware discipline:

* **Auth** — injects the purpose-scoped token into the call so the client never
  has to remember to pass it per call, and the server always sees a credential.
  It never forwards a *caller* identity — only the server's purpose token
  (FR-9.3).
* **Structured logging** — one log line per call with server, tool, and outcome,
  so a run's MCP traffic is inspectable.
* **Transient-only retry** — retries a call only when the failure is transient
  (`errors.is_retryable`); a logic error, a validation refusal, or a permission
  denial is never retried, exactly as on the agent side. Retrying a
  deterministic refusal would just fail slower while hiding the real cause.

Interceptors are async (the MCP transport is async). They are deliberately thin:
the real permission decision is the server's, not the interceptor's — an
interceptor that "helpfully" retried a `PermissionDenied` would undermine the
whole point of the server-side check.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ap_agent.config import get_settings
from ap_agent.errors import is_retryable

logger = logging.getLogger("ap_agent.mcp")

# Type alias kept loose (the adapter's request/result types are internal); the
# interceptor Protocol is structural, so matching the call shape is enough.
_Handler = Callable[[Any], Awaitable[Any]]


class AuthInterceptor:
    """Inject the purpose-scoped token into every call's arguments.

    The token is set once on the client (per purpose) and added to the tool
    args here, so individual call sites never handle credentials. Only the
    server's purpose token is sent — never a propagated caller identity.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    async def __call__(self, request: Any, handler: _Handler) -> Any:
        args = dict(getattr(request, "args", {}) or {})
        args.setdefault("token", self._token)
        request = _with_args(request, args)
        return await handler(request)


class LoggingInterceptor:
    """One structured log line per MCP tool call, with outcome."""

    async def __call__(self, request: Any, handler: _Handler) -> Any:
        name = getattr(request, "name", "?")
        server = getattr(request, "server_name", "?")
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001 - logged then re-raised
            logger.info("mcp.tool_call server=%s tool=%s outcome=error err=%s", server, name, exc)
            raise
        logger.info("mcp.tool_call server=%s tool=%s outcome=ok", server, name)
        return result


class TransientRetryInterceptor:
    """Retry a call only on a transient failure (FR-8.1).

    Bounded, with backoff, and only for exceptions `errors.is_retryable`
    positively classifies as transient. A permission denial or a validation
    error is a deterministic refusal and is re-raised immediately — never
    retried.
    """

    def __init__(self, *, attempts: int, initial_delay: float, backoff_factor: float) -> None:
        self._attempts = attempts
        self._initial_delay = initial_delay
        self._backoff = backoff_factor

    async def __call__(self, request: Any, handler: _Handler) -> Any:
        delay = self._initial_delay
        for attempt in range(1, self._attempts + 1):
            try:
                return await handler(request)
            except Exception as exc:  # noqa: BLE001 - re-raised unless transient
                if not is_retryable(exc) or attempt == self._attempts:
                    raise
                await asyncio.sleep(delay)
                delay *= self._backoff
        raise RuntimeError("retry loop fell through")  # pragma: no cover


def _with_args(request: Any, args: dict[str, Any]) -> Any:
    """Return the request carrying updated args.

    `MCPToolCallRequest` is a pydantic model, so `model_copy(update=...)` is the
    correct, immutable way to set the merged args. If a future adapter version
    made it a plain object, a direct attribute set is the fallback.
    """
    if hasattr(request, "model_copy"):
        return request.model_copy(update={"args": args})
    request.args = args
    return request


def default_interceptors(token: str) -> list[Any]:
    """The standard onion for an MCP client: auth (outermost) → logging →
    transient retry (innermost, closest to the call)."""
    s = get_settings()
    return [
        AuthInterceptor(token),
        LoggingInterceptor(),
        TransientRetryInterceptor(
            attempts=s.transient_retry_attempts,
            initial_delay=s.transient_retry_initial_delay,
            backoff_factor=s.transient_retry_backoff_factor,
        ),
    ]
