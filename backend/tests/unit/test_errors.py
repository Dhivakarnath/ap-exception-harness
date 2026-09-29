"""The fail-loud contract, asserted.

The single most important property: an unrecognised failure is FATAL, never
retried and never masked. Covers FR-8.1, FR-8.3, FR-8.4, FR-8.5; ADR-007.
"""

from __future__ import annotations

from ap_agent.errors import (
    BudgetExceededError,
    CredentialsError,
    ErrorClass,
    ErrorContext,
    ExtractionError,
    HumanInputRequiredError,
    PolicyViolationError,
    ToolArgumentError,
    TransientError,
    classify_exception,
    is_credentials_error,
    is_retryable,
)


class TestClassificationDefaultsToFatal:
    def test_unknown_exception_is_fatal(self) -> None:
        # The conservative direction: never guess that something is safe.
        assert classify_exception(ValueError("bad maths")) is ErrorClass.FATAL

    def test_unknown_exception_is_not_retryable(self) -> None:
        assert is_retryable(RuntimeError("logic bug")) is False

    def test_zero_division_is_fatal(self) -> None:
        assert classify_exception(ZeroDivisionError()) is ErrorClass.FATAL


class TestTransientDetection:
    def test_named_transient_exception_is_transient(self) -> None:
        class ReadTimeout(Exception):
            pass

        assert classify_exception(ReadTimeout()) is ErrorClass.TRANSIENT
        assert is_retryable(ReadTimeout()) is True

    def test_retryable_http_status_is_transient(self) -> None:
        class Boom(Exception):
            status_code = 429

        assert classify_exception(Boom()) is ErrorClass.TRANSIENT

    def test_client_error_status_is_not_transient(self) -> None:
        class Boom(Exception):
            status_code = 400

        assert classify_exception(Boom()) is ErrorClass.FATAL

    def test_botocore_throttling_shape_is_transient(self) -> None:
        class ClientError(Exception):
            response = {
                "Error": {"Code": "ThrottlingException"},
                "ResponseMetadata": {"HTTPStatusCode": 429},
            }

        assert classify_exception(ClientError()) is ErrorClass.TRANSIENT

    def test_botocore_validation_shape_is_fatal(self) -> None:
        class ClientError(Exception):
            response = {
                "Error": {"Code": "ValidationException"},
                "ResponseMetadata": {"HTTPStatusCode": 400},
            }

        assert classify_exception(ClientError()) is ErrorClass.FATAL


class TestOnlyTransientIsRetryable:
    def test_each_class_retryability(self) -> None:
        cases = [
            (TransientError("blip"), True),
            (BudgetExceededError("tokens", 100, 101), False),
            (ToolArgumentError("bad arg"), False),
            (HumanInputRequiredError("ambiguous"), False),
            (PolicyViolationError("sod_check", "agent cannot approve"), False),
            (ExtractionError("schema mismatch"), False),
        ]
        for err, expected in cases:
            assert err.retryable is expected, f"{type(err).__name__} retryable != {expected}"


class TestCredentialsDetection:
    """An expired/invalid credential is an auth failure, not a reading failure.
    INC-019: relabel it so the message names the real fix, but never reclassify
    a genuine extraction failure as an auth problem."""

    def test_botocore_expired_token_shape_is_credentials_error(self) -> None:
        class ClientError(Exception):
            response = {
                "Error": {"Code": "ExpiredTokenException"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            }

        assert is_credentials_error(ClientError()) is True

    def test_expired_token_by_message_fallback(self) -> None:
        exc = Exception(
            "An error occurred (ExpiredTokenException) when calling the Converse "
            "operation: The security token included in the request is expired"
        )
        assert is_credentials_error(exc) is True

    def test_no_credentials_error_by_class_name(self) -> None:
        class NoCredentialsError(Exception):
            pass

        assert is_credentials_error(NoCredentialsError()) is True

    def test_schema_validation_shape_is_not_credentials(self) -> None:
        class ClientError(Exception):
            response = {
                "Error": {"Code": "ValidationException"},
                "ResponseMetadata": {"HTTPStatusCode": 400},
            }

        assert is_credentials_error(ClientError()) is False

    def test_plain_value_error_is_not_credentials(self) -> None:
        assert is_credentials_error(ValueError("total did not parse")) is False

    def test_credentials_error_is_fatal_and_not_retryable(self) -> None:
        err = CredentialsError("token expired; refresh your AWS login")
        assert err.error_class is ErrorClass.FATAL
        assert err.retryable is False


class TestExtractionNeverCoerces:
    def test_extraction_failure_is_fatal(self) -> None:
        # FR-2.4: we do not coerce or best-guess invoice fields. Wrong numbers
        # silently accepted are worse than a failed run.
        assert ExtractionError("total did not parse").error_class is ErrorClass.FATAL


class TestDiagnosticPayload:
    def test_payload_carries_full_context(self) -> None:
        ctx = ErrorContext(
            stage="three_way_match",
            run_id="run-1",
            trace_id="trace-1",
            tenant_id="manufacturing-demo",
            document_id="doc-1",
            inputs={"invoice_id": "INV-001"},
        )
        payload = PolicyViolationError(
            "doa_route", "amount exceeds tier", context=ctx
        ).as_dict()

        for key in (
            "error_type",
            "error_class",
            "message",
            "retryable",
            "raised_at",
            "stage",
            "run_id",
            "trace_id",
            "tenant_id",
            "document_id",
            "inputs",
        ):
            assert key in payload, f"diagnostic payload missing {key}"

        assert payload["error_class"] == ErrorClass.POLICY_REJECTED.value
        assert payload["stage"] == "three_way_match"

    def test_cause_traceback_is_preserved(self) -> None:
        try:
            raise ValueError("root cause")
        except ValueError as exc:
            payload = ExtractionError("wrapped", cause=exc).as_dict()

        assert payload["cause"]["type"] == "ValueError"
        assert "root cause" in payload["cause"]["traceback"]

    def test_budget_error_reports_limit_and_actual(self) -> None:
        err = BudgetExceededError("max_tokens_per_run", 120_000, 121_500)
        assert err.limit == 120_000
        assert err.actual == 121_500
        assert "120000" in err.message or "120,000" in err.message
