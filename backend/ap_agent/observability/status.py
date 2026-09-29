"""Observability stack health for the Operations deep-trace panel."""

from __future__ import annotations

import socket
from typing import Any
from urllib.parse import urlparse

from ap_agent.config import get_settings
from ap_agent.observability.langfuse_export import langfuse_enabled, trace_url

# In-process preference for "deep trace new runs" (UI toggle).
_deep_trace_preference: bool = False


def set_deep_trace_preference(enabled: bool) -> None:
    global _deep_trace_preference
    _deep_trace_preference = enabled


def get_deep_trace_preference() -> bool:
    return _deep_trace_preference


def deep_tracing_requested(*, run_override: bool | None = None) -> bool:
    """Whether this run should export OTLP spans.

    Requires the user preference (or per-run override) **and** a configured OTLP
    endpoint. Collector reachability is surfaced in ``observability_status`` for
    the UI but is not re-checked on every run — spans buffer and flush on exit.
    """
    wanted = run_override if run_override is not None else get_deep_trace_preference()
    if not wanted:
        return False
    return get_settings().otel_exporter_otlp_endpoint is not None


def _host_reachable(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _collector_reachable(endpoint: str, timeout: float = 0.35) -> bool:
    parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 4318)
    return _host_reachable(host, port, timeout=timeout)


def _langfuse_web_reachable(host_url: str, timeout: float = 0.35) -> bool:
    parsed = urlparse(host_url if "://" in host_url else f"http://{host_url}")
    web_host = parsed.hostname or "localhost"
    web_port = parsed.port or (443 if parsed.scheme == "https" else 3000)
    return _host_reachable(web_host, web_port, timeout=timeout)


def langfuse_project_url() -> str | None:
    """Canonical Langfuse traces view for the configured local project."""
    settings = get_settings()
    if not settings.langfuse_host:
        return None
    base = settings.langfuse_host.rstrip("/")
    project = settings.langfuse_project_id.strip("/")
    return f"{base}/project/{project}/traces"


def observability_status() -> dict[str, Any]:
    settings = get_settings()
    otel_configured = settings.otel_exporter_otlp_endpoint is not None
    collector_up = (
        otel_configured
        and _collector_reachable(settings.otel_exporter_otlp_endpoint or "")
    )
    lf_on = langfuse_enabled()
    langfuse_url = (settings.langfuse_host or "").rstrip("/") or None
    langfuse_up = bool(langfuse_url and _langfuse_web_reachable(langfuse_url))
    stack_ready = otel_configured and collector_up and lf_on and langfuse_up

    demo_email = settings.langfuse_demo_email
    demo_password = settings.langfuse_demo_password
    login_hint = None
    if langfuse_url and demo_email and demo_password:
        login_hint = f"Langfuse sign-in: {demo_email} / {demo_password}"
    elif langfuse_url:
        login_hint = "Langfuse sign-in: see LANGFUSE_DEMO_EMAIL in .env (docker-compose bootstrap)."

    return {
        "otel_configured": otel_configured,
        "collector_reachable": collector_up,
        "langfuse_configured": lf_on,
        "langfuse_reachable": langfuse_up,
        "langfuse_url": langfuse_url,
        "langfuse_project_url": langfuse_project_url(),
        "deep_traces_available": stack_ready,
        "deep_trace_preference": get_deep_trace_preference(),
        "local_demo_hint": login_hint,
        "trace_url_example": trace_url("0" * 32) if lf_on else None,
    }
