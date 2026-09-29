# Architecture Decision Records

Accepted decisions for this project — including what was deliberately **not** built.

## Portfolio entry (start here)

| Document | Purpose |
|----------|---------|
| **[ADR-001: System architecture (compiled)](ADR-001-system-architecture.md)** | Whole-build synthesis — situation, options, decision, consequences, revisit. Updated when architecture changes materially. |
| **[Post-build report](../post-build-report.md)** | Compiled failures and lessons from [`incident-log.md`](../incident-log.md). |
| **[Business impact / ROI](../roi-framing.md)** | Measured deployment scores vs cited industry benchmarks. |
| **[v2 integration roadmap](../v2-integration-roadmap.md)** | Connector swaps for ingress, ERP, and Slack. |

## Detailed records

| ADR | Title |
|-----|--------|
| [ADR-001](ADR-001-system-architecture.md) | **Compiled** — system architecture (portfolio) |
| [ADR-008](ADR-008-pgvector-in-postgres.md) | pgvector in existing Postgres |
| [ADR-011](ADR-011-no-payment-execution.md) | System terminates at "approved for payment" |
| [ADR-012](ADR-012-po-vs-non-po-routing.md) | PO-backed vs non-PO routing and GL coding |
| [ADR-013](ADR-013-two-stage-extraction.md) | Two-stage extraction (Docling + Bedrock) |
| [ADR-014](ADR-014-live-evaluation-scoring.md) | Live eval: DeepEval + extraction + policy, HITL re-score |
| [ADR-015](ADR-015-three-layer-permissions.md) | Three-layer permissions and agent RBAC |

## Maintaining these documents

- **Incident-level detail** → append to [`docs/incident-log.md`](../incident-log.md).
- **Single-topic decision** → add or update `ADR-0xx-*.md`.
- **Whole-build story** → update [ADR-001](ADR-001-system-architecture.md) and [post-build report](../post-build-report.md) revision sections (do not delete prior dated entries).
