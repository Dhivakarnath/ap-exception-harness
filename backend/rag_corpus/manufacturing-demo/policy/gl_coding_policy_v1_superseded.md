# Manufacturing — GL Coding Policy (SUPERSEDED)

Version: 1.0.0
Effective from: 2025-01-01
Effective to: 2025-12-31

This is the prior year's manufacturing policy, retained for audit but no longer
in force. It must never be cited for an invoice dated in 2026 or later — the
metadata pre-filter excludes any chunk whose `effective_to` is before the
invoice date. If retrieval ever surfaces a clause from this document for a
current invoice, the effective-date filter has failed.

## 1. Inbound Freight (OLD RULE — DO NOT APPLY)

Under the 2025 policy, inbound freight was coded to account 5900, not 5100.
Account 5900 was retired at year end. The current rule (v1.1.0, account 5100)
supersedes this clause entirely. This deliberately-wrong account number exists
so a test can prove the stale clause is never retrieved: a current freight
invoice must cite 5100, never 5900.
