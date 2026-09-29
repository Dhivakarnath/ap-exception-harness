"""Structured errors and the fail-loud policy.

Design commitment (requirements FR-8.3, FR-8.4, FR-8.5; ADR-007):

    A masked failure in an AP system means a wrong payment authorisation that
    nobody notices. That is strictly worse than a visible crash.

Therefore:
  * There are no silent fallbacks, degraded modes, or coerced outputs.
  * Retries exist ONLY for transient I/O and are classified explicitly.
  * Everything else propagates, carrying enough context to diagnose it:
    stage, tenant, run id, trace id, inputs, and the original exception.

`ErrorClass` is the single place the system decides how a failure is handled.
Anything that is not provably transient / model-recoverable / human-fixable is
`FATAL` and surfaces loudly in the UI error panel.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ErrorClass(StrEnum):
    """How a failure is to be handled. See design §13."""

    TRANSIENT = "transient"
    """Network blip, timeout, 429. Eligible for bounded retry with backoff."""

    BUDGET = "budget"
    """A run budget was exceeded (steps/tokens/time/tool calls). Halt + escalate."""

    MODEL_RECOVERABLE = "model_recoverable"
    """Bad tool arguments the model can plausibly correct if told."""

    HUMAN_FIXABLE = "human_fixable"
    """Missing or ambiguous data. Route to HITL; do not guess."""

    POLICY_REJECTED = "policy_rejected"
    """A guardrail or policy check refused the action. Expected, not a bug."""

    FATAL = "fatal"
    """Everything else. Propagate. Never mask."""


@dataclass(slots=True)
class ErrorContext:
    """Diagnostic envelope attached to every failure."""

    stage: str
    run_id: str | None = None
    trace_id: str | None = None
    tenant_id: str | None = None
    document_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "inputs": self.inputs,
        }


class APAgentError(Exception):
    """Base class for all first-party errors.

    Carries an `ErrorClass` so the harness never has to guess whether something
    is safe to retry.
    """

    error_class: ErrorClass = ErrorClass.FATAL

    def __init__(
        self,
        message: str,
        *,
        context: ErrorContext | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context = context
        self.cause = cause
        self.raised_at = datetime.now(UTC)

    @property
    def retryable(self) -> bool:
        return self.error_class is ErrorClass.TRANSIENT

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the UI error panel and the audit log.

        Deliberately verbose: a reviewer must be able to diagnose from this
        alone, without shell access.
        """
        payload: dict[str, Any] = {
            "error_type": type(self).__name__,
            "error_class": self.error_class.value,
            "message": self.message,
            "retryable": self.retryable,
            "raised_at": self.raised_at.isoformat(),
        }
        if self.context is not None:
            payload |= self.context.as_dict()
        if self.cause is not None:
            payload["cause"] = {
                "type": type(self.cause).__name__,
                "message": str(self.cause),
                "traceback": "".join(
                    traceback.format_exception(
                        type(self.cause), self.cause, self.cause.__traceback__
                    )
                ),
            }
        return payload

    def __str__(self) -> str:
        stage = f" [{self.context.stage}]" if self.context else ""
        return f"{self.error_class.value}{stage}: {self.message}"


# --------------------------------------------------------------------- classes


class TransientError(APAgentError):
    """Bounded-retry-eligible I/O failure. The ONLY retryable class (FR-8.1)."""

    error_class = ErrorClass.TRANSIENT


class BudgetExceededError(APAgentError):
    """A run budget tripped. Safety control, not a fallback (NFR-4)."""

    error_class = ErrorClass.BUDGET

    def __init__(
        self,
        budget_name: str,
        limit: float,
        actual: float,
        *,
        context: ErrorContext | None = None,
    ) -> None:
        super().__init__(
            f"Run budget '{budget_name}' exceeded: {actual} > {limit}. "
            "Halting and escalating rather than continuing.",
            context=context,
        )
        self.budget_name = budget_name
        self.limit = limit
        self.actual = actual


class ToolArgumentError(APAgentError):
    """Model produced arguments the tool cannot accept."""

    error_class = ErrorClass.MODEL_RECOVERABLE


class HumanInputRequiredError(APAgentError):
    """Data is missing or ambiguous. Escalate; never fabricate."""

    error_class = ErrorClass.HUMAN_FIXABLE


class PolicyViolationError(APAgentError):
    """A policy/guardrail refused an action.

    This is the system working correctly. It is an error only in the sense that
    the requested action will not proceed.
    """

    error_class = ErrorClass.POLICY_REJECTED

    def __init__(
        self,
        rule: str,
        reason: str,
        *,
        context: ErrorContext | None = None,
    ) -> None:
        super().__init__(f"Policy '{rule}' refused the action: {reason}", context=context)
        self.rule = rule
        self.reason = reason


class ExtractionError(APAgentError):
    """Extraction failed schema validation.

    Explicitly FATAL: we do not coerce or best-guess invoice fields
    (FR-2.4). Wrong numbers silently accepted are worse than a failed run.
    """

    error_class = ErrorClass.FATAL


class CredentialsError(APAgentError):
    """A provider call failed authentication, not extraction.

    An expired or missing AWS credential is not a malformed model reading, and
    reporting it as one ("the model did not return a valid extraction") sends
    whoever is debugging in the wrong direction — they inspect the invoice and
    the prompt when the real fix is a one-line credential refresh. Fatal (there
    is no safe way to proceed without credentials) but distinct, so the message
    can name the actual remedy. See INC-019.
    """

    error_class = ErrorClass.FATAL


class ParsingError(APAgentError):
    """Document could not be parsed into a usable structure."""

    error_class = ErrorClass.FATAL


class RetrievalError(APAgentError):
    """Retrieval failed or returned unusable grounding."""

    error_class = ErrorClass.FATAL


class ConfigurationError(APAgentError):
    """Configuration is missing or incoherent."""

    error_class = ErrorClass.FATAL


class PolicyPackInvariantError(APAgentError):
    """A policy pack reached the engine violating an invariant its own schema
    should have enforced (e.g. `agent_may_approve_above_touchless=True`).

    Distinct from `ap_agent.core.policy_pack.PolicyPackError`, which is raised
    at *load* time. This is the engine's own defence-in-depth re-check at
    *evaluation* time — for a pack that was somehow constructed without going
    through `PolicyPack.model_validate` (a hand-built object in a test or
    script, say). Fatal: this is a control violation, not a data problem to
    route around.
    """

    error_class = ErrorClass.FATAL


# ------------------------------------------------------------------ utilities

# Exception types from third-party libraries that are safe to treat as
# transient. Kept as an explicit allow-list: anything not listed here is FATAL
# by default, which is the conservative direction.
_TRANSIENT_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "ReadError",
        "RemoteProtocolError",
        "TimeoutException",
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "ModelTimeoutException",
        "InternalServerException",
        "OperationalError",
    }
)

_TRANSIENT_HTTP_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})


def classify_exception(exc: BaseException) -> ErrorClass:
    """Classify a third-party exception.

    Default is FATAL. An exception is only transient when we can positively
    identify it as such — we never guess in the permissive direction.
    """
    if isinstance(exc, APAgentError):
        return exc.error_class

    if type(exc).__name__ in _TRANSIENT_EXCEPTION_NAMES:
        return ErrorClass.TRANSIENT

    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and status in _TRANSIENT_HTTP_STATUS:
        return ErrorClass.TRANSIENT

    # botocore-style error shape
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str) and code in _TRANSIENT_EXCEPTION_NAMES:
            return ErrorClass.TRANSIENT
        http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if isinstance(http_status, int) and http_status in _TRANSIENT_HTTP_STATUS:
            return ErrorClass.TRANSIENT

    return ErrorClass.FATAL


def is_retryable(exc: BaseException) -> bool:
    """Predicate for retry middleware. Transient only (FR-8.1)."""
    return classify_exception(exc) is ErrorClass.TRANSIENT


# Provider error codes / exception names that mean "authentication failed",
# not "the request was malformed". Matched by botocore `Error.Code`, by
# exception class name (botocore's `NoCredentialsError`), and by substring for
# the SSO-token case that arrives worded slightly differently across SDKs.
_CREDENTIALS_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
        "UnrecognizedClientException",
        "InvalidSignatureException",
        "AccessDeniedException",
        "UnauthorizedException",
        "AuthFailure",
    }
)

_CREDENTIALS_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "NoCredentialsError",
        "CredentialRetrievalError",
        "TokenRetrievalError",
        "SSOTokenLoadError",
        "UnauthorizedSSOTokenError",
    }
)


def is_credentials_error(exc: BaseException) -> bool:
    """Whether an exception is an authentication/credential failure.

    Conservative and positive: only a recognised credential-error code or class
    counts. The point is to relabel a genuinely misdiagnosed failure (an expired
    AWS token surfacing as "the model returned nothing"), never to reclassify a
    real extraction failure as an auth problem.
    """
    if type(exc).__name__ in _CREDENTIALS_EXCEPTION_NAMES:
        return True

    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str) and code in _CREDENTIALS_ERROR_CODES:
            return True

    # Fallback: some SDK layers wrap the token-expiry message without a clean
    # botocore error shape. Keep this narrow and specific.
    message = str(exc).lower()
    return "security token" in message and "expired" in message
