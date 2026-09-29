.DEFAULT_GOAL := help
SHELL := /bin/bash
BE := backend

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------- setup
.PHONY: install
install: ## Install backend deps (core + dev + parse)
	cd $(BE) && uv sync --extra dev --extra parse

.PHONY: install-all
install-all: ## Install everything incl. ml (torch) and eval extras
	cd $(BE) && uv sync --extra dev --extra parse --extra ml --extra eval

.PHONY: env
env: ## Create .env from the template if absent
	@[ -f .env ] || (cp .env.example .env && echo "created .env — review it")

# ------------------------------------------------------------------ services
.PHONY: up
up: ## Start core services (Postgres + mock ERP)
	docker compose up -d postgres mock-erp

.PHONY: up-full
up-full: ## Start full stack (Postgres, mock ERP, backend, frontend, HITL lifecycle)
	docker compose up -d postgres mock-erp backend frontend hitl-lifecycle

.PHONY: hitl-lifecycle
hitl-lifecycle: ## Expire stale HITL reviews and sweep terminated checkpoints
	cd $(BE) && uv run python scripts/hitl_lifecycle.py

.PHONY: down
down: ## Stop services (volumes retained)
	docker compose --profile observability down

.PHONY: ps
ps: ## Service status
	docker compose --profile observability ps

.PHONY: logs
logs: ## Tail service logs
	docker compose logs -f --tail=100

# ------------------------------------------------------------- observability
.PHONY: obs-up
obs-up: ## Start the full Langfuse v4 + OTLP collector stack (heavy; profile observability)
	docker compose --profile observability up -d

.PHONY: obs-down
obs-down: ## Stop the observability stack (volumes retained)
	docker compose --profile observability down

.PHONY: obs-collector-logs
obs-collector-logs: ## Tail the OTLP collector logs (spans print here = proof of the generic OTLP path)
	docker compose --profile observability logs -f --tail=100 otel-collector

.PHONY: obs-live
obs-live: ## Live+MCP invoice with an emitter + OTEL/Langfuse export: spans go to the collector and Langfuse (needs obs-up + AWS creds + corpus)
	cd $(BE) && uv run python scripts/observability_live.py --mcp

# ------------------------------------------------------------------ migrations
.PHONY: migrate
migrate: ## Apply migrations to head
	cd $(BE) && uv run alembic upgrade head

.PHONY: migration
migration: ## Autogenerate a migration (make migration m="add x")
	cd $(BE) && uv run alembic revision --autogenerate -m "$(m)"

.PHONY: migrate-down
migrate-down: ## Roll back one migration
	cd $(BE) && uv run alembic downgrade -1

.PHONY: db-reset
db-reset: ## Drop and rebuild the schema (destroys local data)
	cd $(BE) && uv run alembic downgrade base && uv run alembic upgrade head

# --------------------------------------------------------------------- checks
.PHONY: lint
lint: ## Ruff lint (backend + mock ERP)
	cd $(BE) && uv run ruff check .
	cd $(BE) && uv run ruff check ../mock_erp --config pyproject.toml

.PHONY: fmt
fmt: ## Ruff format + autofix
	cd $(BE) && uv run ruff format . && uv run ruff check --fix .

.PHONY: types
types: ## mypy strict (backend + fixtures + mock ERP)
	cd $(BE) && uv run mypy ap_agent apfixtures
	cd $(BE) && MYPYPATH=../mock_erp uv run mypy ../mock_erp/app --ignore-missing-imports

.PHONY: test
test: ## Unit tests
	cd $(BE) && uv run pytest -m "not integration and not bedrock and not ml"

.PHONY: test-int
test-int: ## Integration tests (services must be up)
	cd $(BE) && uv run pytest -m integration

.PHONY: check
check: lint types test ## Lint + types + unit tests

# ----------------------------------------------------------------------- data
.PHONY: dataset
dataset: ## Generate the adversarial dataset (50 cases per failure mode)
	cd $(BE) && uv run python -m apfixtures.build

.PHONY: dataset-quick
dataset-quick: ## Generate 3 cases per mode (iteration only, not for reported results)
	cd $(BE) && uv run python -m apfixtures.build --per-mode 3

.PHONY: dataset-verify
dataset-verify: ## Verify generated artefacts against the manifest
	cd $(BE) && uv run python -m apfixtures.build --verify-only

.PHONY: parse-demo
parse-demo: ## Parse one invoice per format and show the recovered structure
	cd $(BE) && uv run python scripts/inspect_parse.py --all

.PHONY: extract-demo
extract-demo: ## Extract one invoice per format via live Nova Lite (needs AWS creds)
	cd $(BE) && uv run python scripts/extract_demo.py --all

.PHONY: policy-demo
policy-demo: ## Run the adversarial dataset through the policy engine alone (no LLM)
	cd $(BE) && uv run python scripts/policy_demo.py --all

.PHONY: rag-ingest
rag-ingest: ## Embed and store the GL-coding policy corpus (needs AWS creds)
	cd $(BE) && uv run python scripts/rag_demo.py --ingest

.PHONY: rag-demo
rag-demo: ## GL-code a sample non-PO invoice with cited policy (needs AWS creds)
	cd $(BE) && uv run python scripts/rag_demo.py

.PHONY: supervisor-demo
supervisor-demo: ## Run whole invoices through the supervisor graph, in-process (no AWS)
	cd $(BE) && uv run python scripts/supervisor_demo.py --all

.PHONY: supervisor-demo-live
supervisor-demo-live: ## Same, but with live Bedrock Nova Lite + Titan retrieval (needs AWS creds + ingested corpus)
	cd $(BE) && uv run python scripts/supervisor_demo.py --all --live

.PHONY: mcp-defense-demo
mcp-defense-demo: ## Defence in depth: bypass agent RBAC, show the MCP server still refuses a read-only write
	cd $(BE) && uv run python scripts/mcp_defense_demo.py

.PHONY: supervisor-demo-fullstack
supervisor-demo-fullstack: ## Full stack: live Bedrock+Titan AND ERP over the real MCP server (needs AWS creds + corpus)
	cd $(BE) && uv run python scripts/supervisor_demo.py --all --live --mcp

.PHONY: hitl-demo
hitl-demo: ## HITL: a high-value invoice pauses, notifies, and resumes on a human decision (no AWS)
	cd $(BE) && uv run python scripts/hitl_demo.py

.PHONY: hitl-demo-live
hitl-demo-live: ## Same, but pause/resume on top of live Bedrock+Titan and the MCP ERP (needs AWS creds + corpus)
	cd $(BE) && uv run python scripts/hitl_demo.py --live --mcp

.PHONY: obs-demo
obs-demo: ## Observability: stream a run's RunEvents live (check ledger, decision, cost) + the wire contract
	cd $(BE) && uv run python scripts/observability_demo.py

.PHONY: warm-models
warm-models: ## Pre-download Docling layout + OCR weights (first run pulls ~30 MB)
	cd $(BE) && uv run python -c "\
from ap_agent.ingest.parsing import get_converter; \
get_converter(with_ocr=False); get_converter(with_ocr=True); \
print('docling models ready')"

.PHONY: eval
eval: ## Run deterministic eval gate (policy adherence on manifest)
	cd $(BE) && uv run pytest evals/ -m "not eval_live" -q

.PHONY: eval-live
eval-live: ## Real Bedrock DeepEval agent/tool/RAG + extraction gate (AWS in shell)
	cd $(BE) && uv run python scripts/run_eval.py --live

.PHONY: eval-report
eval-report: ## Print scorecard JSON (smoke, no gate failure)
	cd $(BE) && uv run python scripts/run_eval.py --smoke --no-gate

.PHONY: prune-live-runs
prune-live-runs: ## Remove all DB runs except the three live upload invoices (see script for --keep)
	cd $(BE) && uv run python scripts/prune_live_runs.py

.PHONY: prune-live-runs
prune-live-runs: ## Remove all DB runs except the three live upload invoices (see script --keep)
	cd $(BE) && uv run python scripts/prune_live_runs.py

.PHONY: roi-metrics
roi-metrics: ## Latency percentiles + KPIs from persisted runs (Postgres required)
	cd $(BE) && uv run python scripts/query_roi_metrics.py

# ------------------------------------------------------------------ run apps
.PHONY: api
api: ## Run the backend API (reload)
	cd $(BE) && uv run uvicorn ap_agent.api.main:app --reload --port 8080

.PHONY: web
web: ## Run the frontend dev server
	cd frontend && npm run dev

.PHONY: web-install
web-install: ## Install frontend dependencies
	cd frontend && npm install

.PHONY: web-build
web-build: ## Type-check + production-build the frontend
	cd frontend && npm run build

.PHONY: web-check
web-check: ## Frontend lint + typecheck + unit tests
	cd frontend && npm run lint && npm run typecheck && npm run test
