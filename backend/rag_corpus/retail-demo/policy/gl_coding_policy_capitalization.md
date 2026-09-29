# Retail — Capitalization and Expense-Threshold Policy

Version: 2.0.0
Effective from: 2026-01-01

This policy sets the boundary between an item that is expensed to one of the
seven codeable accounts and an item that is a candidate capital asset requiring
a human decision. The automated coder never capitalises an asset on its own;
where a line crosses the threshold, it abstains and routes to a human. The rule
protects against silently expensing a capital asset (which distorts the P&L) and
against silently capitalising an expense (which distorts the balance sheet).

## 1. The capitalization threshold

The capitalization threshold is 2,500 per single item or asset. A single item
below 2,500 is expensed to its normal account. A single item at or above 2,500
is a candidate capital asset and must be routed to a human for a capitalisation
decision. The threshold is applied per item, not per invoice: ten keyboards at
40 each is a 400 office/computer expense, not a capital asset, because no single
item reaches the threshold.

## 2. Sub-threshold items are expensed normally

An item below the threshold is coded to its normal account: sub-threshold
computer hardware to 6420, sub-threshold perpetual software to 7200,
sub-threshold office furniture to 6410, and so on. Being below the threshold
does not change which account an item belongs to; it only confirms that the item
is an expense rather than a capital asset.

## 3. At-or-above-threshold items route to a human

A single computer, laptop, server, display, or piece of equipment at or above
2,500 must be routed to a human for a capitalisation decision — never expensed
to 6420 automatically. A perpetual software licence at or above 2,500 must be
routed to a human — never expensed to 7200 automatically. A furniture or fit-out
purchase at or above 2,500 must be routed to a human — never expensed to 6410
automatically. In each case the account it *would* take if expensed is noted for
the human, but the code is not applied.

## 4. Bulk purchases of low-value items

A bulk purchase of many low-value items — fifty headsets, a pallet of paper — is
expensed to its normal account (6420, 6410) even when the invoice total exceeds
the threshold, because the threshold is per item. Do not route a high-total,
low-unit-value invoice to a human as if it were a capital asset; code it to its
normal account.

## 5. Ambiguous or mixed-threshold invoices

When an invoice mixes sub-threshold and at-or-above-threshold items, split it:
expense the sub-threshold lines to their normal accounts and route the
at-or-above-threshold line to a human. Cite the threshold policy for the routed
line and the relevant coding clause for each expensed line. Never let a single
capital item drag an entire mixed invoice to a human, and never let sub-threshold
lines pull a capital item into an expense account.

## 6. When in doubt on threshold

If the per-item value cannot be determined from the invoice — a lump-sum line
with no unit breakdown that could be one capital asset or many small items — do
not assume it is sub-threshold. Route it to a human. Assuming sub-threshold to
enable an auto-code is exactly the silent error this policy exists to prevent.
