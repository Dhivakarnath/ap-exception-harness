"""Optional LangChain callbacks for the current model invoke (Slice 13 evals).

DeepEval's `CallbackHandler` must reach each `ChatBedrockConverse.invoke` call.
The supervisor graph is not a single LangChain agent runnable, so callbacks are
threaded through a contextvar rather than mutating global clients.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_langchain_callbacks: ContextVar[list[Any] | None] = ContextVar(
    "_langchain_callbacks", default=None
)


def langchain_invoke_config() -> dict[str, Any] | None:
    """Config dict for `Runnable.invoke(..., config=...)`, or None."""
    callbacks = _langchain_callbacks.get()
    if not callbacks:
        return None
    return {"callbacks": callbacks}


@contextmanager
def langchain_callbacks(callbacks: list[Any] | None) -> Iterator[None]:
    """Scope LangChain callbacks to nested model/graph invokes."""
    token = _langchain_callbacks.set(callbacks)
    try:
        yield
    finally:
        _langchain_callbacks.reset(token)
