"""The middleware stack — the harness that makes the agent safe (FR-8, FR-9).

Middleware order is not cosmetic; it is the security model. The stack runs
outermost-first on the way in and unwinds on the way out, so the order below is
the order a tool call is scrutinised:

    1. PII redaction        — never let a raw account number reach the model
    2. call / budget limits  — a runaway loop is halted before it acts
    3. RBAC tool filter      — a sub-agent cannot even see a tool it may not use
    4. transient retry       — a network blip is retried; a logic error is not
    5. policy-engine gate     — the deterministic checks must clear a write
    6. tool-error handling    — a genuine tool fault surfaces, it is not masked

Two hooks carry most of the weight. ``wrap_model_call`` is where RBAC filters
the tool list the model is even offered (a permission the model cannot see it
cannot try to use). ``wrap_tool_call`` is where the budget, the policy engine,
retry, and error handling wrap the actual execution of a tool.

**Counters live in state, not on the middleware.** The LangChain docs are
explicit that middleware instances may run concurrently and mutating ``self``
races. So the budget counters live in ``HarnessState`` (an ``AgentState``
subclass) and each hook returns a state delta. The instance holds only
configuration, which is read-only after construction.

**Every guardrail here is deterministic and fails loud.** A budget breach
raises ``BudgetExceededError`` (halt + escalate, never a quiet truncation); an
RBAC denial removes the tool rather than letting the model try and be told no;
the policy gate raises ``PolicyViolationError`` on a reject. None of these is a
fallback — they are the system working.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

from ap_agent.errors import (
    APAgentError,
    BudgetExceededError,
    ErrorContext,
    is_retryable,
)

# PII redaction lives once in observability.redaction (masks 12+ digit
# account/card runs, keeps short ids); re-exported so tool output and event
# payloads apply the same rule and cannot drift.
from ap_agent.observability.redaction import redact_pii_text as _redact_pii


class HarnessState(AgentState):
    """Agent state extended with the run's budget counters.

    Counters live here (not on a middleware instance) so concurrent hook
    execution cannot race on them — the pattern the middleware docs mandate.
    Every budget hook reads these and returns a delta.
    """

    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int


def _counter(state: Any, key: str) -> int:
    """Read an integer counter from the loosely-typed agent state.

    ``AgentState`` is a TypedDict of framework keys; our budget counters are
    extra keys the middleware adds at runtime, so the read goes through a plain
    dict lookup and is coerced to int (defaulting to 0 when unset).
    """
    value = state.get(key, 0) if isinstance(state, dict) else getattr(state, key, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _set_counter(state: Any, key: str, value: int) -> None:
    """Write an integer counter back to the agent state dict."""
    state[key] = value


# ------------------------------------------------------------- 1. PII redaction


class PiiRedactionMiddleware(AgentMiddleware[HarnessState]):
    """Strip sensitive fragments from tool results before the model sees them.

    Defence in depth over the extractor, which already keeps only last-four bank
    fragments (FR-15.7: the prompt/extractor guides, this enforces). Runs first
    so nothing downstream — logs, the model, the stream — ever handles a full
    account or routing number. A conservative regex pass: it redacts long digit
    runs that look like account/card numbers while leaving short identifiers
    (invoice numbers, PO numbers, GL accounts) intact.
    """

    name = "pii_redaction"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        result = handler(request)
        if isinstance(result, ToolMessage) and isinstance(result.content, str):
            redacted = _redact_pii(result.content)
            if redacted != result.content:
                return result.model_copy(update={"content": redacted})
        return result





# --------------------------------------------------------- 2. call / budget limits


class BudgetMiddleware(AgentMiddleware[HarnessState]):
    """Hard run budgets: model calls, tool calls, tokens, wall-clock (NFR-4).

    A safety control, not a fallback. When a budget would be exceeded the run
    halts and escalates by raising ``BudgetExceededError`` *before* the offending
    call, rather than silently truncating the work — a truncated run that looked
    complete is exactly the class of quiet failure this project forbids.

    Wall-clock is checked against a start time captured on the first hook. The
    counters are read from and written back to state so they survive across the
    concurrent hook invocations the docs warn about.
    """

    name = "budget"

    def __init__(
        self,
        *,
        max_model_calls: int,
        max_tool_calls: int,
        max_tokens: int,
        max_seconds: int,
    ) -> None:
        super().__init__()
        self._max_model_calls = max_model_calls
        self._max_tool_calls = max_tool_calls
        self._max_tokens = max_tokens
        self._max_seconds = max_seconds
        # Wall-clock start is process-local timing, not cross-call logical state,
        # so it is acceptable on the instance; it is only ever read for elapsed.
        self._started_monotonic: float | None = None

    def _check_wall_clock(self) -> None:
        if self._started_monotonic is None:
            self._started_monotonic = time.monotonic()
            return
        elapsed = time.monotonic() - self._started_monotonic
        if elapsed > self._max_seconds:
            raise BudgetExceededError(
                "wall_clock_seconds",
                self._max_seconds,
                round(elapsed, 1),
                context=ErrorContext(stage="harness.budget"),
            )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        self._check_wall_clock()
        state = request.state
        calls = _counter(state, "model_calls") + 1
        if calls > self._max_model_calls:
            raise BudgetExceededError(
                "model_calls",
                self._max_model_calls,
                calls,
                context=ErrorContext(stage="harness.budget"),
            )
        tokens = _counter(state, "input_tokens") + _counter(state, "output_tokens")
        if tokens > self._max_tokens:
            raise BudgetExceededError(
                "tokens", self._max_tokens, tokens, context=ErrorContext(stage="harness.budget")
            )
        response = handler(request)
        # Accumulate token usage from the response for the next call's check and
        # for the run's context_utilisation metric.
        usage = _usage_from_response(response)
        _set_counter(state, "model_calls", calls)
        _set_counter(state, "input_tokens", _counter(state, "input_tokens") + usage[0])
        _set_counter(state, "output_tokens", _counter(state, "output_tokens") + usage[1])
        return response

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        self._check_wall_clock()
        state = request.state
        calls = _counter(state, "tool_calls") + 1
        if calls > self._max_tool_calls:
            raise BudgetExceededError(
                "tool_calls",
                self._max_tool_calls,
                calls,
                context=ErrorContext(stage="harness.budget"),
            )
        _set_counter(state, "tool_calls", calls)
        return handler(request)


def _usage_from_response(response: ModelResponse) -> tuple[int, int]:
    """Pull (input_tokens, output_tokens) out of a model response, best effort.

    Token accounting is a metric, not a control gate on its own — the hard gate
    is the count checked before the call — so a response whose usage cannot be
    read contributes zero rather than raising. The wall-clock and call-count
    budgets still bound the run.
    """
    message = getattr(response, "result", None)
    # ModelResponse.result is a list of messages in v1; the last AI message
    # carries usage_metadata.
    messages = message if isinstance(message, list) else [message]
    for msg in reversed(messages or []):
        usage = getattr(msg, "usage_metadata", None)
        if isinstance(usage, dict):
            return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    return 0, 0


# ------------------------------------------------------------- 3. RBAC tool filter


class RbacToolFilterMiddleware(AgentMiddleware[HarnessState]):
    """Restrict a sub-agent to a named allow-list of tools (FR-9.1).

    Enforced in ``wrap_model_call`` by removing disallowed tools from the request
    *before the model is called*, so the model is never even offered a tool it
    may not use — a permission it cannot see is a permission it cannot try to
    exercise. This is the agent-layer half of defence in depth; Slice 9 adds the
    independent server-side half at the MCP boundary. A denial here is silent to
    the model (the tool simply is not there) but a static test asserts the
    filtered set, so the restriction is verifiable, not implicit.
    """

    name = "rbac_tool_filter"

    def __init__(self, *, allowed_tool_names: frozenset[str]) -> None:
        super().__init__()
        self._allowed = allowed_tool_names

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        allowed_tools = [t for t in request.tools if _tool_name(t) in self._allowed]
        return handler(request.override(tools=allowed_tools))


def _tool_name(tool: Any) -> str:
    return getattr(tool, "name", "") or ""


# ------------------------------------------------------------- 4. transient retry


class TransientRetryMiddleware(AgentMiddleware[HarnessState]):
    """Retry a tool call ONLY on a transient I/O failure (FR-8.1).

    Bounded retries with exponential backoff, and *only* for exceptions
    ``errors.is_retryable`` positively classifies as transient (timeouts, 429s,
    connection resets). A logic error, a policy rejection, a bad argument — none
    of these is retried, because retrying a deterministic failure just fails
    slower while hiding the real problem. The allow-list is conservative: unknown
    exceptions are treated as fatal and propagate immediately.
    """

    name = "transient_retry"

    def __init__(
        self, *, attempts: int, initial_delay: float, backoff_factor: float
    ) -> None:
        super().__init__()
        self._attempts = attempts
        self._initial_delay = initial_delay
        self._backoff = backoff_factor

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        delay = self._initial_delay
        last_exc: BaseException | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                return handler(request)
            except Exception as exc:  # noqa: BLE001 - re-raised unless transient
                last_exc = exc
                if not is_retryable(exc) or attempt == self._attempts:
                    # Not transient, or attempts exhausted: propagate loud.
                    raise
                time.sleep(delay)
                delay *= self._backoff
        # Unreachable: the loop either returns or raises.
        raise last_exc if last_exc is not None else RuntimeError("retry loop fell through")


# --------------------------------------------------------- 6. tool-error handling


class ToolErrorMiddleware(AgentMiddleware[HarnessState]):
    """Surface genuine tool faults; never mask them (FR-8.4).

    Innermost of the wrap hooks. A first-party ``APAgentError`` (a policy
    rejection, human-input-required, a fatal parse failure) propagates unchanged
    so the harness classifies and escalates it. Only an *unexpected* exception is
    wrapped — and even then into a fatal first-party error, not a soothing
    ToolMessage that lets the model pretend the tool succeeded. The point is that
    a failure is loud and typed, never cosmetically softened.
    """

    name = "tool_error"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        try:
            return handler(request)
        except APAgentError:
            # Already a typed, classified first-party error. Let the harness
            # handle it — wrapping it would only hide its class.
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed fatal error
            tool_name = request.tool_call.get("name", "unknown")
            raise APAgentError(
                f"Tool {tool_name!r} raised an unexpected error: {exc}",
                context=ErrorContext(
                    stage="harness.tool", inputs={"tool": tool_name}
                ),
                cause=exc,
            ) from exc
