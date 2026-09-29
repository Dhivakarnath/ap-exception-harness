"""Application lifecycle — HITL review expiry and checkpoint sweep on startup."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger("ap_agent.lifecycle")


def run_hitl_lifecycle() -> dict[str, int]:
    """Expire stale pending reviews and sweep terminated graph checkpoints.

    Safe to call on every API startup. Audit rows are never deleted — only
    graph checkpoint state is swept (see `checkpointing.py`).
    """
    from ap_agent.agent.checkpointing import (
        expire_stale_reviews,
        make_checkpointer,
        sweep_checkpoints,
        terminated_thread_ids,
    )
    from ap_agent.persistence.db import session_scope

    expired = 0
    swept = 0
    try:
        with session_scope() as session:
            expired = expire_stale_reviews(session)
            session.commit()
    except Exception:
        logger.exception("HITL review expiry failed")
        return {"expired": 0, "swept": 0}

    saver = make_checkpointer()
    if saver is None:
        return {"expired": expired, "swept": 0}

    try:
        with session_scope() as session:
            thread_ids = terminated_thread_ids(session)
        swept = sweep_checkpoints(saver, thread_ids=thread_ids)
    except Exception:
        logger.exception("Checkpoint sweep failed")

    if expired or swept:
        logger.info("HITL lifecycle: expired=%s swept=%s", expired, swept)
    return {"expired": expired, "swept": swept}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    run_hitl_lifecycle()
    yield
