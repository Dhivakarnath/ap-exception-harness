"""DeepEval CallbackHandler wiring (offline)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.eval


def test_callback_handler_importable() -> None:
    pytest.importorskip("deepeval")
    from deepeval.integrations.langchain import CallbackHandler

    handler = CallbackHandler(thread_id="eval-test")
    assert handler is not None


def test_invoke_context_merges_callbacks() -> None:
    from ap_agent.llm.invoke_context import langchain_callbacks, langchain_invoke_config

    class _Cb:
        pass

    cb = _Cb()
    assert langchain_invoke_config() is None
    with langchain_callbacks([cb]):
        cfg = langchain_invoke_config()
        assert cfg is not None
        assert cfg["callbacks"] == [cb]
