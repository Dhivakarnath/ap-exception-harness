# Retail — GL Coding Policy for Non-PO Spend (SUPERSEDED)

Version: 1.0.0
Effective from: 2025-01-01
Effective to: 2025-12-31

This is the prior year's policy, retained for audit but no longer in force. It
must never be cited for an invoice dated in 2026 or later — the metadata
pre-filter excludes any chunk whose `effective_to` is before the invoice date.
If retrieval ever surfaces a clause from this document for a current invoice,
the effective-date filter has failed.

## 1. Software Subscriptions (OLD RULE — DO NOT APPLY)

Under the 2025 policy, software subscriptions were coded to account 7000, not
7200. Account 7000 was retired at year end. An invoice coded to 7000 today is
wrong; the current rule (v2.0.0, account 7200) supersedes this clause entirely.
This deliberately-wrong account number exists so a test can prove the stale
clause is never retrieved: a current run must cite 7200, never 7000.
