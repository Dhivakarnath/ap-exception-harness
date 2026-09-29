"""Config must fail loudly. These tests assert the absence of silent defaults.

Covers requirements NFR-8, FR-8.4, FR-11.3, FR-12.4.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ap_agent.config import EmbeddingProvider, Env, Settings

BASE: dict[str, object] = {
    "database_url": "postgresql+psycopg://ap:ap@localhost:5432/ap_agent",
    "_env_file": None,  # isolate from a developer's real .env
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**BASE, **overrides})  # type: ignore[arg-type]


class TestRequiredValues:
    def test_missing_database_url_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_non_postgres_dsn_is_rejected(self) -> None:
        # pgvector is required, so a non-Postgres DSN is a configuration bug.
        with pytest.raises(ValidationError, match="PostgreSQL DSN"):
            _settings(database_url="sqlite:///local.db")

    def test_unknown_env_key_is_rejected(self) -> None:
        # extra="forbid": a typo'd variable must not be silently ignored.
        with pytest.raises(ValidationError):
            _settings(totally_unknown_setting="x")


class TestCoherence:
    def test_top_k_cannot_exceed_candidate_pool(self) -> None:
        with pytest.raises(ValidationError, match="cannot exceed"):
            _settings(retrieval_candidates=5, retrieval_top_k=10)

    def test_slack_channel_requires_credentials(self) -> None:
        # An undeliverable HITL escalation is unacceptable (FR-11.3).
        with pytest.raises(ValidationError, match="slack_bot_token"):
            _settings(notify_channel="slack")

    def test_slack_channel_accepted_when_fully_configured(self) -> None:
        s = _settings(
            notify_channel="slack",
            slack_bot_token="xoxb-test",
            slack_channel="#ap-approvals",
        )
        assert s.notify_channel == "slack"

    def test_partial_langfuse_config_is_rejected(self) -> None:
        # Half-configured observability is a silent blind spot (FR-12.4).
        with pytest.raises(ValidationError, match="Langfuse requires"):
            _settings(langfuse_host="http://localhost:3000")

    def test_console_notifications_rejected_in_production(self) -> None:
        with pytest.raises(ValidationError, match="not permitted in production"):
            _settings(env=Env.PRODUCTION, notify_channel="console")


class TestEnvCoercionRegressions:
    """Regressions for INC-001 and INC-002 (see docs/incident-log.md)."""

    def test_numeric_literal_accepts_env_string(self) -> None:
        # INC-001: env vars arrive as strings; Literal[int] does not coerce.
        assert _settings(embedding_dimensions="1024").embedding_dimensions == 1024

    def test_invalid_dimension_still_rejected(self) -> None:
        # Coercion must not weaken validation.
        with pytest.raises(ValidationError):
            _settings(embedding_dimensions="777")

    def test_blank_optional_value_is_treated_as_unset(self) -> None:
        # INC-002: `LANGFUSE_HOST=` means "not configured", not "empty string".
        s = _settings(langfuse_host="", langfuse_public_key="", langfuse_secret_key="")
        assert s.langfuse_host is None
        assert s.langfuse_enabled is False

    def test_blank_slack_values_do_not_satisfy_slack_channel(self) -> None:
        with pytest.raises(ValidationError, match="slack_bot_token"):
            _settings(notify_channel="slack", slack_bot_token="", slack_channel="")


class TestDefaults:
    def test_model_defaults_to_nova_lite(self) -> None:
        assert _settings().bedrock_model_id == "amazon.nova-lite-v1:0"

    def test_run_budgets_are_present(self) -> None:
        s = _settings()
        assert s.max_model_calls_per_run > 0
        assert s.max_tool_calls_per_run > 0
        assert s.max_tokens_per_run > 0
        assert s.max_run_seconds > 0

    def test_embedding_provider_defaults_to_bedrock(self) -> None:
        assert _settings().embedding_provider is EmbeddingProvider.BEDROCK

    def test_observability_flags_are_derived_not_assumed(self) -> None:
        s = _settings()
        assert s.langfuse_enabled is False
        assert s.otel_enabled is False

        configured = _settings(
            langfuse_host="http://localhost:3000",
            langfuse_public_key="pk",
            langfuse_secret_key="sk",
        )
        assert configured.langfuse_enabled is True
        assert configured.otel_enabled is True
