"""Application configuration.

Design commitment (requirements NFR-8, FR-8.4): configuration fails **loudly**.
There are no silent defaults for anything that affects correctness, money, or
credentials. A missing required value raises at import/startup, not at the
moment an invoice is being paid.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(RuntimeError):
    """Raised when configuration is missing or internally inconsistent.

    Deliberately a hard failure. See ADR-007 (fail-loud policy).
    """


class Env(StrEnum):
    LOCAL = "local"
    CI = "ci"
    PRODUCTION = "production"


class EmbeddingProvider(StrEnum):
    """Dense embedding backend.

    BEDROCK  -> Amazon Titan Text Embeddings V2 (keeps auth/billing on Bedrock)
    LOCAL    -> sentence-transformers (CPU, zero API cost, self-contained)
    """

    BEDROCK = "bedrock"
    LOCAL = "local"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="forbid",  # unknown env keys are a config bug, not a warning
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- runtime
    env: Env = Env.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # -------------------------------------------------------------- database
    database_url: str = Field(
        ...,
        description="Postgres DSN. pgvector extension required.",
    )

    # --------------------------------------------------------------- bedrock
    aws_region: str = Field(default="us-east-1")
    bedrock_model_id: str = Field(
        default="amazon.nova-lite-v1:0",
        description="Primary reasoning model. Nova Lite by default (ADR-001).",
    )
    bedrock_embedding_model_id: str = Field(default="amazon.titan-embed-text-v2:0")

    # Per-million-token on-demand price of the reasoning model, used to turn the
    # captured token usage into a USD cost (FR-12.5). Defaults are Amazon Nova
    # Lite's published on-demand rate ($0.06 in / $0.24 out per 1M tokens). This
    # is config, not code: a price change or a different model is an env change.
    # The derived cost is only as current as these numbers — a stated assumption,
    # not a silent one.
    model_price_input_per_mtok_usd: float = Field(default=0.06, ge=0.0)
    model_price_output_per_mtok_usd: float = Field(default=0.24, ge=0.0)

    # ------------------------------------------------------------- retrieval
    embedding_provider: EmbeddingProvider = EmbeddingProvider.BEDROCK
    embedding_dimensions: Literal[256, 512, 1024] = 1024
    local_embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    retrieval_candidates: int = Field(default=20, ge=1, le=200)
    retrieval_top_k: int = Field(default=4, ge=1, le=50)
    coding_retrieval_top_k: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Final cross-encoder-ranked context sent to the GL coding model.",
    )

    # ---------------------------------------------------- external endpoints
    mock_erp_base_url: str = "http://localhost:8091"
    erp_mcp_command: str = "python -m ap_agent.mcp_servers.erp_server"
    notify_mcp_command: str = "python -m ap_agent.mcp_servers.notify_server"
    # Which ERP transport the supervisor's ERP client uses. "in_process" is the
    # fast in-process seam (Slice 8); "mcp" runs the ERP MCP server over stdio
    # with independent server-side permission enforcement (Slice 9). Both satisfy
    # the same `ErpClient` Protocol, so the supervisor is identical either way.
    erp_transport: Literal["in_process", "mcp"] = "in_process"

    # ----------------------------------------------------------- observability
    otel_exporter_otlp_endpoint: str | None = None
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_project_id: str = "ap-agent"
    langfuse_demo_email: str | None = None
    langfuse_demo_password: str | None = None
    otel_service_name: str = "ap-exception-agent"

    # Evaluate completed real-document runs asynchronously with DeepEval using
    # the configured Bedrock model as judge. Scripted/demo runs are excluded.
    live_evals_enabled: bool = True

    # ------------------------------------------------------------ run budgets
    # NFR-4: hard budgets prevent runaway loops. These are safety controls,
    # not fallbacks.
    max_model_calls_per_run: int = Field(default=25, ge=1)
    max_tool_calls_per_run: int = Field(default=40, ge=1)
    max_tokens_per_run: int = Field(default=120_000, ge=1)
    max_run_seconds: int = Field(default=180, ge=1)

    # ------------------------------------------------ retry (transient only)
    # FR-8.1: retries apply ONLY to transient I/O. Never to logic failures.
    transient_retry_attempts: int = Field(default=3, ge=1, le=10)
    transient_retry_initial_delay: float = Field(default=1.0, gt=0)
    transient_retry_backoff_factor: float = Field(default=2.0, ge=1.0)

    # ------------------------------------------------- HITL checkpointing
    # The graph checkpointer persists a paused (interrupt) run so it can resume.
    # "postgres" is durable across a process restart — required in production,
    # because an AP review can wait days for a human. "in_memory" is for tests
    # and offline demos (fast, no DB), where cross-restart durability is moot.
    checkpointer_backend: Literal["in_memory", "postgres"] = "in_memory"
    # A terminated or abandoned paused run's graph checkpoint is swept after this
    # many days. This TTL is on the *resumable graph state only* — never on the
    # `hitl_reviews`/`audit_log` rows, which are the audit system of record and
    # do not expire (a stale review escalates or is marked `expired`, not
    # deleted).
    checkpoint_ttl_days: int = Field(default=30, ge=1, le=365)
    # A pending review left unactioned this long is transitioned pending ->
    # expired (recorded, escalated), never deleted.
    review_expiry_days: int = Field(default=14, ge=1, le=365)

    # ------------------------------------------------------------- tenancy
    default_tenant: str = "manufacturing-demo"
    policy_pack_dir: Path = REPO_ROOT / "backend" / "policy_packs"
    # Content-addressed storage root for user-uploaded invoices (the transparency
    # UI's "run my own document" path). Local filesystem for the demo; an S3
    # implementation of the same `DocumentStore` protocol is the v2 swap.
    upload_store_dir: Path = REPO_ROOT / "backend" / "var" / "uploads"

    # ------------------------------------------------------- notifications
    notify_channel: Literal["console", "slack", "email"] = "console"
    slack_bot_token: str | None = None
    slack_channel: str | None = None

    # ------------------------------------------------------------------ api
    # Browser origins allowed to call the API (the transparency UI). The Vite
    # dev server defaults to 5173; a comma-separated env var overrides this for
    # a real deployment. CORS is a browser security control, so it is an explicit
    # allowlist, never "*".
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    # ---------------------------------------------------------- validators
    @field_validator(
        "otel_exporter_otlp_endpoint",
        "langfuse_host",
        "langfuse_public_key",
        "langfuse_secret_key",
        "slack_bot_token",
        "slack_channel",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        # `FOO=` in a .env file means "not configured", not "empty string".
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("embedding_dimensions", mode="before")
    @classmethod
    def _coerce_dimensions(cls, v: object) -> object:
        # Env vars arrive as strings; Literal[int] does not coerce them.
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return v

    @field_validator("database_url")
    @classmethod
    def _validate_dsn(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError(
                "database_url must be a PostgreSQL DSN (pgvector is required — see ADR-008)"
            )
        return v

    @model_validator(mode="after")
    def _validate_coherence(self) -> Settings:
        if self.retrieval_top_k > self.retrieval_candidates:
            raise ValueError(
                f"retrieval_top_k ({self.retrieval_top_k}) cannot exceed "
                f"retrieval_candidates ({self.retrieval_candidates})"
            )
        if self.coding_retrieval_top_k > self.retrieval_candidates:
            raise ValueError(
                f"coding_retrieval_top_k ({self.coding_retrieval_top_k}) cannot exceed "
                f"retrieval_candidates ({self.retrieval_candidates})"
            )

        if self.notify_channel == "slack" and not (self.slack_bot_token and self.slack_channel):
            raise ValueError(
                "notify_channel='slack' requires slack_bot_token and slack_channel. "
                "Refusing to start with a notification channel that cannot deliver "
                "HITL escalations (FR-11.3)."
            )

        langfuse_parts = (self.langfuse_host, self.langfuse_public_key, self.langfuse_secret_key)
        if any(langfuse_parts) and not all(langfuse_parts):
            raise ValueError(
                "Langfuse requires host, public key, and secret key together. "
                "Partial observability config is a silent blind spot (FR-12.4)."
            )

        if self.env is Env.PRODUCTION and self.notify_channel == "console":
            raise ValueError(
                "notify_channel='console' is not permitted in production: "
                "HITL escalations would go nowhere."
            )

        return self

    # ------------------------------------------------------------- helpers
    @property
    def langfuse_enabled(self) -> bool:
        return all((self.langfuse_host, self.langfuse_public_key, self.langfuse_secret_key))

    @property
    def otel_enabled(self) -> bool:
        return self.otel_exporter_otlp_endpoint is not None or self.langfuse_enabled


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once, failing loudly with an actionable message."""
    try:
        return Settings()
    except ValidationError as exc:
        details = "\n".join(
            f"  - {'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
            for e in exc.errors()
        )
        raise ConfigurationError(
            "Invalid or incomplete configuration. "
            "Copy .env.example to .env and fill in the required values.\n"
            f"{details}"
        ) from exc
