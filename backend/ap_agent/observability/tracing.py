"""OpenTelemetry instrumentation — a span per node, tool, model call, and check
(FR-12.1).

Tracing is a *transparency* control, not a debugging afterthought: a reviewer
must be able to open a run and see the nested structure — the run span, the
stage spans under it, the tool and model-call spans under those, and one span
per deterministic check — with the same threshold/actual attributes the check
ledger shows. That nesting is what turns "the run took 4 seconds" into "the
three-way match took 12ms and the Nova Lite coding call took 3.1s".

Config-gated and fail-safe: when `otel_exporter_otlp_endpoint` is unset, tracing
is a **no-op** — the context managers still work (so call sites are unchanged)
but no tracer is created and nothing is exported. Instrumentation must never be
the reason a run fails, so a tracing error is swallowed (logged), never
propagated; the run's correctness does not depend on whether a span was
recorded.

The tracer is built once and cached. Spans are created through small context
managers (`run_span`, `node_span`, `tool_span`, `model_span`, `check_span`) that
attach the right attributes; because they use the OTEL context, spans opened
inside an outer span nest under it automatically, which is exactly the tree a
reviewer wants.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import sys
from collections.abc import Iterator
from typing import Any

from ap_agent.config import get_settings

logger = logging.getLogger("ap_agent.tracing")

_TRACER: Any | None = None
_TRACER_INITIALISED = False

# Per-run gate: spans export only inside ``active_deep_tracing(True)``.
_DEEP_TRACE_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "deep_trace_active", default=False
)


def _traces_endpoint(base: str) -> str:
    """Resolve the OTLP/HTTP *traces* URL from the configured base endpoint.

    The `OTEL_EXPORTER_OTLP_ENDPOINT` setting is a **base** URL (e.g.
    ``http://localhost:4318``), matching the OTEL environment-variable
    convention. The HTTP exporter, however, only appends the signal path
    (``/v1/traces``) when it reads that env var itself — when an ``endpoint=`` is
    passed to the constructor it is used *verbatim*. Passing the bare base would
    therefore POST to ``/`` and the collector (which serves ``/v1/traces``) would
    silently drop every span. So we append the signal path ourselves, while
    tolerating a value that already includes it (idempotent).
    """
    trimmed = base.rstrip("/")
    if trimmed.endswith("/v1/traces"):
        return trimmed
    return f"{trimmed}/v1/traces"


@contextlib.contextmanager
def active_deep_tracing(enabled: bool) -> Iterator[None]:
    """Enable or disable span export for the current run context."""
    token = _DEEP_TRACE_ACTIVE.set(enabled)
    try:
        yield
    finally:
        _DEEP_TRACE_ACTIVE.reset(token)


def is_deep_tracing_active() -> bool:
    return _DEEP_TRACE_ACTIVE.get()


def _get_tracer() -> Any | None:
    """The process tracer, or None when OTEL is not configured.

    Built once. When no OTLP endpoint is configured, tracing is off and this
    returns None — every span context manager then becomes a no-op.
    """
    global _TRACER, _TRACER_INITIALISED
    if _TRACER_INITIALISED:
        return _TRACER

    _TRACER_INITIALISED = True
    settings = get_settings()
    if not settings.otel_exporter_otlp_endpoint:
        _TRACER = None
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=_traces_endpoint(settings.otel_exporter_otlp_endpoint)
                )
            )
        )
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("ap_agent")
    except Exception as exc:  # noqa: BLE001 - instrumentation must not break a run
        logger.warning("OTEL tracer init failed; tracing disabled: %s", exc)
        _TRACER = None
    return _TRACER


@contextlib.contextmanager
def _span(name: str, attributes: dict[str, Any]) -> Iterator[Any]:
    """Open a span with attributes, or a no-op when tracing is off.

    Only failures in the *instrumentation itself* — creating the span or setting
    its attributes — are swallowed (logged), since correctness does not depend on
    a span being recorded. An exception raised by the *wrapped body* must
    propagate untouched: this system is fail-loud, and a traced node that raises
    has to raise exactly as an untraced one would. Conflating the two (catching
    the body's exception here) would both hide real failures and — because a
    ``@contextmanager`` may not yield again after an exception is thrown in —
    corrupt the generator protocol. So the body is yielded *outside* the guarded
    setup, and its exceptions are never caught here (the span's own context
    manager still records the error status on the way out).
    """
    if not _DEEP_TRACE_ACTIVE.get():
        yield None
        return

    tracer = _get_tracer()
    span_cm = None
    span = None
    if tracer is not None:
        try:
            span_cm = tracer.start_as_current_span(name)
            span = span_cm.__enter__()
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
        except Exception as exc:  # noqa: BLE001 - never let instrumentation fail a run
            logger.debug("span %s setup failed: %s", name, exc)
            span_cm = None
            span = None

    if span_cm is None:
        # Tracing off or span setup failed: a transparent no-op that still
        # propagates any body exception.
        yield span
        return

    # Delegate to the span's own context manager so it records exception status
    # and end-time correctly, while letting the body's exception propagate.
    try:
        yield span
    except BaseException:
        if not span_cm.__exit__(*sys.exc_info()):
            raise
    else:
        span_cm.__exit__(None, None, None)


def current_trace_id_hex() -> str | None:
    """The active OTEL trace id as a 32-char hex string, if tracing is on."""
    tracer = _get_tracer()
    if tracer is None:
        return None
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return None
        return format(ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001
        return None


@contextlib.contextmanager
def run_span(*, run_id: str, tenant_id: str, invoice_id: str) -> Iterator[Any]:
    """The outermost span for a whole run. Everything else nests under it."""
    with _span(
        "ap.run",
        {"ap.run_id": run_id, "ap.tenant_id": tenant_id, "ap.invoice_id": invoice_id},
    ) as span:
        yield span


@contextlib.contextmanager
def run_span_capture(
    *,
    run_id: str,
    tenant_id: str,
    invoice_id: str,
    on_trace_id: Any | None = None,
) -> Iterator[Any]:
    """Like ``run_span``, but invokes ``on_trace_id(hex)`` when the span opens."""
    with run_span(run_id=run_id, tenant_id=tenant_id, invoice_id=invoice_id) as span:
        if on_trace_id is not None:
            tid = current_trace_id_hex()
            if tid:
                try:
                    on_trace_id(tid)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("on_trace_id callback failed: %s", exc)
        yield span


def node_span(name: str) -> Any:
    """A supervisor graph node (extract / code_gl / policy / decide / hitl)."""
    return _span(f"ap.node.{name}", {"ap.node": name})


def tool_span(name: str) -> Any:
    """A tool invocation. Args are PII-redacted before they reach a span."""
    return _span(f"ap.tool.{name}", {"ap.tool": name})


def model_span(*, model_id: str, purpose: str) -> Any:
    """A model call (extraction or coding), for cost/latency attribution.

    Carries both our own ``ap.*`` attributes and the OpenTelemetry GenAI
    semantic-convention attributes (``gen_ai.*``). The latter are what an
    LLM-native backend like Langfuse reads to classify this span as a
    *generation* (rather than a plain span) and to slot it into its model views.
    We set the request-side attributes that are known when the call starts
    (system + model + operation); token counts and cost are recorded honestly
    elsewhere — in the run's own cost events and the KPI metrics computed from
    real Bedrock usage — rather than being stamped onto the span before the
    response exists.
    """
    return _span(
        "ap.model",
        {
            "ap.model_id": model_id,
            "ap.purpose": purpose,
            "gen_ai.system": "aws.bedrock",
            "gen_ai.request.model": model_id,
            "gen_ai.operation.name": purpose,
        },
    )


def check_span(check_attributes: dict[str, Any]) -> Any:
    """One deterministic check. `check_attributes` is `CheckResult.as_span_attributes()`."""
    name = str(check_attributes.get("check.name", "check"))
    return _span(f"ap.check.{name}", check_attributes)


def set_model_usage(span: Any, *, input_tokens: int, output_tokens: int) -> None:
    """Record token usage on a model span after the call returns.

    Token counts are only known once the model has responded, so they are set on
    the (already open) `model_span` here rather than at span creation. Uses the
    OpenTelemetry GenAI ``gen_ai.usage.*`` attribute names, which is what an
    LLM-native backend like Langfuse reads to show the generation's token cost.
    A no-op when the span is ``None`` (tracing off), so call sites are unchanged.
    """
    if span is None:
        return
    try:
        span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens))
        span.set_attribute("gen_ai.usage.output_tokens", int(output_tokens))
        # Some backends read the OTEL-native spellings too; set both cheaply.
        span.set_attribute("gen_ai.usage.prompt_tokens", int(input_tokens))
        span.set_attribute("gen_ai.usage.completion_tokens", int(output_tokens))
    except Exception as exc:  # noqa: BLE001 - instrumentation must not fail a run
        logger.debug("set_model_usage failed: %s", exc)


def force_flush(timeout_millis: int = 10_000) -> bool:
    """Flush pending spans to the exporter now; returns True if a flush ran.

    Spans go through a ``BatchSpanProcessor``, which buffers and exports on its
    own schedule. A long-lived API process never needs this, but a short-lived
    script (a demo or a verification run) can exit before the batch is sent, so
    its spans would never reach the collector — making an export look broken when
    it is only unflushed. This forces the provider to drain its queue. A no-op
    (returns False) when tracing is unconfigured, so call sites stay unchanged.
    """
    if not _DEEP_TRACE_ACTIVE.get() or _get_tracer() is None:
        return False
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        flush = getattr(provider, "force_flush", None)
        if flush is None:
            return False
        flush(timeout_millis)
        return True
    except Exception as exc:  # noqa: BLE001 - flushing must not fail a run either
        logger.debug("force_flush failed: %s", exc)
        return False


def reset_tracer_for_test() -> None:
    """Clear the cached tracer so a test can re-evaluate config. Test-only."""
    global _TRACER, _TRACER_INITIALISED
    _TRACER = None
    _TRACER_INITIALISED = False
    _DEEP_TRACE_ACTIVE.set(False)
