# Manufacturing — PO-Inheritance and Non-PO Handling Policy

Version: 1.1.0
Effective from: 2026-01-01

This policy explains how coding responsibility divides between the purchase
order and this corpus. Because the manufacturing tenant is overwhelmingly
PO-backed, the default and correct source of a GL account is the purchase order;
this corpus only governs the exceptional non-PO invoice.

## 1. PO-backed invoices inherit their account

A PO-backed invoice takes its GL account, cost center, and entity from the
matched purchase order. The coder does not re-derive the account for a PO-backed
invoice; it inherits. A three-way match (PO, receipt, invoice) confirms the
inheritance. This policy does not override a PO's coding.

## 2. Non-PO invoices use this corpus

Only an invoice with no purchase order uses the coding clauses in this corpus.
For such an invoice, the account is derived from the materials/freight/services
guides and cited to the relevant clause or precedent.

## 3. Missing-PO check comes first

Because a genuine non-PO materials or freight purchase is rare here, a non-PO
goods invoice should first be checked for a *missing* PO reference — a PO that
exists but was not matched — before coding it as non-PO. A missing PO is more
likely than a true non-PO materials buy.

## 4. Non-PO invoices outside the three accounts

A non-PO invoice that is not materials (5000), freight (5100), or contracted
services (6500) has no codeable account for this tenant and routes to a human.
The coder does not stretch a non-PO office or software invoice into a retail
account that does not apply to this tenant.

## 5. Abstain when inheritance is unclear

If it cannot be determined whether an invoice is PO-backed or genuinely non-PO,
route it to a human rather than guessing an account. Coding a PO-backed invoice
from this corpus, or a non-PO invoice as if it inherited, are both errors this
policy exists to prevent.
