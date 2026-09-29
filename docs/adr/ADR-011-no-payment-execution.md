# ADR-011: System terminates at "approved for payment"

## Status
Accepted

## Context
AP automation must not move money. Payment execution belongs in the customer's ERP under existing treasury controls.

## Decision
The system posts approve/hold/flag decisions only. No payment tool, scope, or MCP endpoint exists. The mock ERP asserts `supports_payment_execution: false`.

## Consequences
- Safer regulatory boundary
- HITL and SoD focus on coding and approval, not disbursement
- Real ERP connectors in v2 inherit the same contract
