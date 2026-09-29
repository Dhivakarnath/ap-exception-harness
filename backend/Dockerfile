# AP Exception Agent API — production image
# Build from repo root: docker build -f backend/Dockerfile .
FROM python:3.12-slim

WORKDIR /app/backend

RUN pip install --no-cache-dir uv==0.9.5

COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/ap_agent ./ap_agent
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY backend/policy_packs ./policy_packs
COPY backend/rag_corpus ./rag_corpus
COPY backend/scripts ./scripts
COPY mock_erp /app/mock_erp

# The API serves real document parsing and asynchronous DeepEval scoring; both
# are production dependencies for this image, not developer tooling. Coding
# retrieval uses bounded hybrid RRF, avoiding a multi-GB cross-encoder startup.
RUN uv sync --frozen --no-dev --extra parse --extra eval

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/backend:/app/mock_erp
EXPOSE 8080

WORKDIR /app/backend
CMD ["uv", "run", "uvicorn", "ap_agent.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
