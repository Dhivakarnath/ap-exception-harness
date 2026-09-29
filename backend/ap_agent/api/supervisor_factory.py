"""Build `Supervisor` instances for the API — shared between run trigger and resume.

Centralises deps assembly so run and cross-process resume use the same
checkpointer backend, policy pack, and ERP wiring. Production resume rebuilds
a fresh `Supervisor` from Postgres checkpoint state (proven in
`test_checkpoint_durability.py`); the in-process `_supervisors` cache is an
optimisation only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

from ap_agent.agent.checkpointing import make_checkpointer
from ap_agent.agent.supervisor import Supervisor, SupervisorDeps
from ap_agent.config import get_settings
from ap_agent.core.policy_pack import load_policy_pack


def _ensure_mock_erp_importable() -> None:
    mock_erp = Path(__file__).resolve().parents[3] / "mock_erp"
    if mock_erp.is_dir() and str(mock_erp) not in sys.path:
        sys.path.insert(0, str(mock_erp))


def _erp_client(*, mcp: bool) -> Any:
    if mcp:
        from ap_agent.mcp_servers.erp_mcp_client import McpErpClient

        return McpErpClient()
    _ensure_mock_erp_importable()
    from ap_agent.agent.erp_client import InProcessErpClient

    return InProcessErpClient()


def _resilient_session_factory() -> Any:
    from ap_agent.persistence.db import session_scope

    return session_scope


def _scripted_coding_and_retriever() -> tuple[Any, Any]:
    from ap_agent.rag.coding import RawGLProposal, ScriptedCodingModel
    from ap_agent.rag.retriever import RetrievedChunk

    citation = "policy/gl_coding_policy_software_subscriptions.md#1"

    class _Retriever:
        def retrieve(self, session: Any, **kwargs: Any) -> list[RetrievedChunk]:
            return [
                RetrievedChunk(
                    chunk_id="ui-demo-chunk",
                    tenant_id="retail-demo",
                    doc_type="policy",
                    source_name="gl_coding_policy_software_subscriptions.md",
                    section="1",
                    citation_ref=citation,
                    version="1",
                    content="SaaS subscriptions code to GL account 7200.",
                    dense_rank=1,
                    lexical_rank=1,
                    rrf_score=0.05,
                )
            ]

    coding = ScriptedCodingModel(
        RawGLProposal(
            gl_account="7200",
            confidence=0.95,
            citation_refs=(citation,),
            reasoning="SaaS subscription.",
        ),
        usage={"input_tokens": 1200, "output_tokens": 180},
    )
    return cast("Any", _Retriever()), coding


def _policy_pack_for(tenant_id: str) -> Any:
    settings = get_settings()
    packs = settings.policy_pack_dir
    for name in (f"{tenant_id}.yaml", "retail_non_po.yaml", "manufacturing.yaml"):
        path = packs / name
        if path.exists():
            return load_policy_pack(path)
    return load_policy_pack(packs / "retail_non_po.yaml")


def build_supervisor_deps(
    *,
    tenant_id: str = "retail-demo",
    live: bool = False,
    mcp: bool = False,
    emitter: Any | None = None,
) -> SupervisorDeps:
    """Assemble injected deps for a run or a durable resume."""
    pack = _policy_pack_for(tenant_id)

    if live:
        from ap_agent.persistence.db import session_scope
        from ap_agent.rag.coding import BedrockCodingModel
        from ap_agent.rag.retriever import HybridRetriever

        deps = SupervisorDeps(
            tenant_id=tenant_id,
            policy=pack,
            erp=_erp_client(mcp=mcp),
            retriever=HybridRetriever(),
            coding_model=BedrockCodingModel(),
            session_factory=session_scope,
            emitter=emitter,
        )
    else:
        retriever, coding = _scripted_coding_and_retriever()
        deps = SupervisorDeps(
            tenant_id=tenant_id,
            policy=pack,
            erp=_erp_client(mcp=False),
            retriever=retriever,
            coding_model=coding,
            session_factory=_resilient_session_factory(),
            emitter=emitter,
        )

    deps.checkpointer = make_checkpointer()
    return deps


def build_supervisor(
    *,
    tenant_id: str = "retail-demo",
    live: bool = False,
    mcp: bool = False,
    emitter: Any | None = None,
) -> Supervisor:
    return Supervisor(
        build_supervisor_deps(tenant_id=tenant_id, live=live, mcp=mcp, emitter=emitter)
    )


def build_upload_supervisor(
    *,
    tenant_id: str = "retail-demo",
    mcp: bool = False,
    emitter: Any | None = None,
    parsed: Any,
    page_images: list[tuple[str, bytes]],
) -> Supervisor:
    """Supervisor for a real uploaded document (always live extraction)."""
    from ap_agent.extract.bedrock import BedrockModelClient
    from ap_agent.extract.extractor import InvoiceExtractor

    supervisor = build_supervisor(
        tenant_id=tenant_id,
        live=True,
        mcp=mcp,
        emitter=emitter,
    )
    supervisor._deps.extractor = InvoiceExtractor(BedrockModelClient())  # noqa: SLF001
    supervisor._deps.parsed_document = parsed  # noqa: SLF001
    supervisor._deps.page_images = page_images  # noqa: SLF001
    return supervisor


def build_resume_supervisor(*, tenant_id: str) -> Supervisor:
    """Fresh supervisor for cross-process HITL resume (Postgres checkpoint required)."""
    settings = get_settings()
    if settings.checkpointer_backend != "postgres":
        raise RuntimeError("Cross-process HITL resume requires CHECKPOINTER_BACKEND=postgres")
    # Resume only re-executes the hitl node; checkpoint carries full state.
    return build_supervisor(tenant_id=tenant_id, live=False, mcp=False, emitter=None)
