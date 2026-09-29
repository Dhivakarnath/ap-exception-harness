"""Dense embeddings behind one protocol (FR-6.2).

Two backends, one interface:

* **Bedrock** — Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`).
  The default, because it keeps auth and billing on the same Bedrock account as
  the reasoning model and needs no local model weights. Titan V2 outputs a
  1024-dimension vector (also configurable to 512/256), which matches the
  `Vector(1024)` column in `policy_chunks`.
* **Local** — `BAAI/bge-m3` via sentence-transformers. Zero API cost and fully
  self-contained, behind the `ml` extra. Its native output is 1024-dim too, so
  the same column and the same stored vectors are dimensionally compatible with
  the Bedrock path — but the two models' vector *spaces* are not
  interchangeable, so a corpus embedded with one backend must be queried with
  the same one. `embedding_model` is recorded per chunk so a mismatch is
  detectable rather than silently returning garbage neighbours.

**Determinism of dimension, not of provider.** Query and corpus must use the
same backend; the module does not convert between spaces. The ingest pipeline
stamps `embedding_model` on every chunk and the retriever asserts the active
model matches, so a backend switch without a re-index fails loudly instead of
degrading retrieval quietly.

Imports are lazy (the `ml` path in particular) so this module loads without
torch present — only actually *using* the local backend requires the extra,
mirroring how `ingest.parsing` defers its Docling import.
"""

from __future__ import annotations

from typing import Any, Protocol

from ap_agent.config import EmbeddingProvider, get_settings
from ap_agent.errors import ConfigurationError, ErrorContext, RetrievalError

# Titan V2's hard input ceiling (per AWS docs: 8,192 tokens / 50k chars). A
# chunk longer than this would be silently truncated by the API, so the
# ingest pipeline is expected to chunk well under it; this constant documents
# the boundary the chunker must respect.
TITAN_V2_MAX_CHARS = 50_000


class EmbeddingModel(Protocol):
    """The narrow surface retrieval and ingest need.

    Two methods, deliberately: documents and a query are embedded by the same
    model but are conventionally distinct calls (some models prepend a
    query/passage instruction), so the split keeps that option open without
    changing callers.
    """

    @property
    def model_id(self) -> str:
        """Identifier stamped onto every stored chunk, for mismatch detection."""
        ...

    @property
    def dimensions(self) -> int:
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...


class BedrockEmbeddingModel:
    """Amazon Titan Text Embeddings V2 via `langchain-aws`."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        settings = get_settings()
        self._model_id = model_id or settings.bedrock_embedding_model_id
        self._region = region or settings.aws_region
        self._dimensions = dimensions or settings.embedding_dimensions
        self._client: Any = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _get_client(self) -> Any:
        if self._client is None:
            from langchain_aws import BedrockEmbeddings

            # `model_kwargs` carries Titan V2's own parameters: the output
            # dimension and L2 normalisation. Normalised vectors make cosine
            # distance (the HNSW index's operator) equivalent to a dot product,
            # which is what the `vector_cosine_ops` index expects.
            self._client = BedrockEmbeddings(
                model_id=self._model_id,
                region_name=self._region,
                model_kwargs={"dimensions": self._dimensions, "normalize": True},
            )
        return self._client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = self._get_client().embed_documents(texts)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vector: list[float] = self._get_client().embed_query(text)
        return vector


class LocalEmbeddingModel:
    """`BAAI/bge-m3` (or configured local model) via sentence-transformers.

    Behind the `ml` extra. Loads the model on first use, not at construction,
    so importing this module never pulls in torch.
    """

    def __init__(self, *, model_name: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.local_embedding_model
        self._model: Any = None
        self._dimensions = 1024  # bge-m3 native output

    @property
    def model_id(self) -> str:
        return f"local:{self._model_name}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - requires ml extra
                raise ConfigurationError(
                    "Local embeddings require the 'ml' extra "
                    "(sentence-transformers, torch). Install it or set "
                    "EMBEDDING_PROVIDER=bedrock.",
                    context=ErrorContext(stage="rag.embeddings.local"),
                    cause=exc,
                ) from exc
            model = SentenceTransformer(self._model_name)
            self._model = model
            dim = model.get_sentence_embedding_dimension()
            if dim is None:
                raise RetrievalError(
                    f"Local embedding model {self._model_name!r} did not report an "
                    "embedding dimension. Refusing to embed against an unknown "
                    "vector width.",
                    context=ErrorContext(stage="rag.embeddings.local"),
                )
            self._dimensions = int(dim)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        # `normalize_embeddings=True` for the same cosine-as-dot-product reason
        # as the Bedrock path.
        vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        model = self._get_model()
        vector = model.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
        return list(vector.tolist())


def get_embedding_model(provider: EmbeddingProvider | None = None) -> EmbeddingModel:
    """Construct the configured embedding backend.

    Fails loud on an unknown provider rather than defaulting — a silently-wrong
    embedding backend would corrupt retrieval in a way that is very hard to
    trace back to configuration.
    """
    settings = get_settings()
    chosen = provider or settings.embedding_provider

    if chosen is EmbeddingProvider.BEDROCK:
        return BedrockEmbeddingModel()
    if chosen is EmbeddingProvider.LOCAL:
        return LocalEmbeddingModel()

    raise ConfigurationError(  # pragma: no cover - enum is exhaustive
        f"Unknown embedding provider {chosen!r}.",
        context=ErrorContext(stage="rag.embeddings"),
    )
