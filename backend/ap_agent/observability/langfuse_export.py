"""Langfuse export — LLM-trace observability, self-hosted, swappable by config
(FR-12.4).

Langfuse is the LLM-native trace backend: it shows a run's model calls with
their prompts, token counts, latency, and cost, deep-linkable per run from the
UI. It is **self-hosted** (the design commitment — no third-party sees invoice
data) and **swappable**: when the three `langfuse_*` settings are unset, this is
a no-op and the system runs identically without it. Observability is never a
hard dependency of processing an invoice.

Langfuse v3+ is OpenTelemetry-based, so it composes with the OTEL tracing rather
than competing with it — the same run span tree can be exported to a generic
OTLP collector *and* enriched into Langfuse. The client is built once and cached;
a construction failure disables the export (logged) rather than failing a run.
"""

from __future__ import annotations

import logging
from typing import Any

from ap_agent.config import get_settings

logger = logging.getLogger("ap_agent.langfuse")

_CLIENT: Any | None = None
_CLIENT_INITIALISED = False


def langfuse_enabled() -> bool:
    """Whether all three Langfuse settings are present (host + keys)."""
    s = get_settings()
    return bool(s.langfuse_host and s.langfuse_public_key and s.langfuse_secret_key)


def get_langfuse() -> Any | None:
    """The Langfuse client, or None when it is not configured.

    Built once. Missing config is not an error — it is the expected default in
    tests and any deployment that has not opted into Langfuse; this returns None
    and callers skip the export.
    """
    global _CLIENT, _CLIENT_INITIALISED
    if _CLIENT_INITIALISED:
        return _CLIENT

    _CLIENT_INITIALISED = True
    if not langfuse_enabled():
        _CLIENT = None
        return None

    settings = get_settings()
    try:
        from langfuse import Langfuse

        _CLIENT = Langfuse(
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
        )
    except Exception as exc:  # noqa: BLE001 - export is optional, never fatal
        logger.warning("Langfuse init failed; export disabled: %s", exc)
        _CLIENT = None
    return _CLIENT


def trace_url(trace_id: str) -> str | None:
    """A deep link to a run's trace in the Langfuse UI, if configured.

    The UI uses this to link a run to its full LLM trace (design §12.2). Returns
    None when Langfuse is not configured, so the UI simply omits the link.
    """
    if not langfuse_enabled():
        return None
    host = get_settings().langfuse_host or ""
    return f"{host.rstrip('/')}/trace/{trace_id}"


def reset_client_for_test() -> None:
    """Clear the cached client so a test can re-evaluate config. Test-only."""
    global _CLIENT, _CLIENT_INITIALISED
    _CLIENT = None
    _CLIENT_INITIALISED = False
