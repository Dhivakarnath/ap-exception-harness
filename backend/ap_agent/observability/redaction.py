"""PII redaction for anything leaving the backend to a client or a log.

The middleware redacts a tool's *string* output; an event payload is a nested
structure (dicts, lists, strings), so redaction has to walk it. This module is
the one place that walk lives, so the rule — mask long digit runs that look like
account or card numbers, leave short identifiers (invoice/PO numbers, GL
accounts, last-four fragments) intact — is applied consistently to every event
payload before it is emitted.

Conservative by construction: it only masks digit runs of 12+, which is the
account/card-number shape. A four-digit GL account, an `INV-001`, a
`PO-2001`, or a stored last-four fragment is left untouched because the reviewer
and the checks legitimately need them, and they are not sensitive.
"""

from __future__ import annotations

import re
from typing import Any

# A digit run of 12+ (optionally spaced/hyphenated), i.e. account/card-like.
_LONG_DIGIT_RUN = re.compile(r"\b\d[\d\s-]{10,}\d\b")


def redact_pii_text(text: str) -> str:
    """Mask account/card-like digit runs in a string, keeping the last four.

    The same rule the tool-output middleware applies, factored here so the event
    emitter and the middleware cannot drift apart.
    """

    def _mask(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 12:
            return match.group(0)
        return f"[REDACTED-{digits[-4:]}]"

    return _LONG_DIGIT_RUN.sub(_mask, text)


def redact_pii_deep(value: Any) -> Any:
    """Recursively redact PII in a nested payload (dict / list / str / scalar).

    Returns a new structure; the input is not mutated. Non-string scalars pass
    through untouched (a numeric total is not PII; a 16-digit *string* that looks
    like an account number is).
    """
    if isinstance(value, str):
        return redact_pii_text(value)
    if isinstance(value, dict):
        return {k: redact_pii_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_pii_deep(v) for v in value]
    return value
