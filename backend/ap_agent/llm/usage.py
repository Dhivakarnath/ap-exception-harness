"""Token-usage capture from a LangChain structured-output response (FR-12.5).

Both model call sites — extraction and GL coding — use
``with_structured_output(schema, include_raw=True)``. With ``include_raw=True``
the result is a dict ``{"parsed", "raw", "parsing_error"}`` where ``raw`` is the
underlying ``AIMessage`` carrying ``usage_metadata`` (the token counts). Without
it, that metadata is discarded and a run's cost cannot be reported — which was
exactly the INC-015 zero-cost defect.

This module is the single definition of "pull the parsed value and the token
usage out of that response", so the extractor and the coder cannot drift in how
they read usage.
"""

from __future__ import annotations

from typing import Any

# The three usage keys we carry through the system. `total_tokens` is kept as a
# reported figure (some providers count it differently than input+output), but
# the budget derives its own total from input+output for internal consistency.
_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def usage_from_raw(raw: Any) -> dict[str, int]:
    """Read ``usage_metadata`` off a raw ``AIMessage`` into a plain int dict.

    Missing or malformed metadata yields zeros rather than raising: a model that
    did not report usage should not fail a run, it should report zero (and be
    visible as such), which is honest about what is known.
    """
    metadata = getattr(raw, "usage_metadata", None) or {}
    if not isinstance(metadata, dict):
        return dict.fromkeys(_USAGE_KEYS, 0)
    return {key: int(metadata.get(key, 0) or 0) for key in _USAGE_KEYS}


def unpack_structured(result: Any) -> tuple[Any, dict[str, int]]:
    """Split an ``include_raw=True`` structured-output result.

    Returns ``(parsed, usage)``. When ``parsed`` is ``None`` (the model returned
    something that did not satisfy the schema) this raises ``SchemaUnpackError``
    carrying the usage, so a failed attempt still accounts for the tokens it
    burned — the caller decides whether to repair or fail. When the response is
    not the ``include_raw`` dict shape (e.g. a scripted stub returning the object
    directly), the value passes through with zero usage.
    """
    if isinstance(result, dict):
        parsed = result.get("parsed")
        usage = usage_from_raw(result.get("raw"))
        if parsed is None:
            detail = str(result.get("parsing_error") or "model returned no parsed object")
            raise SchemaUnpackError(detail, usage)
        return parsed, usage
    return result, dict.fromkeys(_USAGE_KEYS, 0)


def accumulate_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    """Add one call's usage into a running total, in place.

    Failed-and-repaired attempts are summed on purpose: a run that needed two
    calls genuinely cost two calls, and cost reporting that hid the retry would
    understate the price of unreliability.
    """
    for key in _USAGE_KEYS:
        total[key] = total.get(key, 0) + int(usage.get(key, 0) or 0)


def zero_usage() -> dict[str, int]:
    """A fresh zero-usage dict (used to seed an accumulator or a scripted stub)."""
    return dict.fromkeys(_USAGE_KEYS, 0)


class SchemaUnpackError(Exception):
    """Structured output did not satisfy the schema.

    Carries the validation detail (to feed back to the model on a repair turn)
    and the token usage of the failed attempt (so it is still accounted for).
    """

    def __init__(self, detail: str, usage: dict[str, int]) -> None:
        super().__init__(detail)
        self.detail = detail
        self.usage = usage
