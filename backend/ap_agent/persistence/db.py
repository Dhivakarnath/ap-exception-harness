"""Engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ap_agent.config import get_settings
from ap_agent.errors import ConfigurationError, ErrorContext

REQUIRED_EXTENSIONS = ("vector", "pg_trgm")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        # Echo is never enabled implicitly: SQL logs would contain invoice data.
        echo=False,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Rolls back and re-raises on any failure.

    No exception swallowing: a persistence failure must surface (FR-8.5).
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def verify_extensions(engine: Engine | None = None) -> dict[str, str]:
    """Assert required Postgres extensions are present.

    Called at startup. pgvector and pg_trgm are hard requirements — hybrid
    retrieval and fuzzy duplicate detection depend on them, so a missing
    extension is a configuration failure, not a degraded mode.
    """
    engine = engine or get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname = ANY(:names)"
            ),
            {"names": list(REQUIRED_EXTENSIONS)},
        ).all()

    found = {name: version for name, version in rows}
    missing = [e for e in REQUIRED_EXTENSIONS if e not in found]
    if missing:
        raise ConfigurationError(
            f"Missing required Postgres extension(s): {', '.join(missing)}. "
            "Hybrid retrieval (vector) and fuzzy duplicate detection (pg_trgm) "
            "cannot operate without them. Run scripts/init-db.sql.",
            context=ErrorContext(stage="startup.verify_extensions"),
        )
    return found
