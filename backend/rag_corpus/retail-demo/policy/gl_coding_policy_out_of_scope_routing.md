# Retail — Out-of-Scope and Human-Routing Policy

Version: 2.0.0
Effective from: 2026-01-01

This policy enumerates the invoice types that have no codeable account in this
chart and must always be routed to a human. It exists as an explicit, citable
statement of what the automated coder must *not* do, so that abstention is a
grounded decision rather than a failure to find a match. Each clause below is a
reason to return an empty coding with low confidence and route to a human. There
is no account to cite for these; the citation is this policy clause itself,
recording *why* the item is out of scope.

Under no circumstances should any item in this policy be forced into 5000, 5100,
6410, 6420, 6500, 6600, or 7200 to avoid routing to a human. A wrong code that
looks confident is worse than an honest abstention.

## 1. Utilities

Electricity, water, gas, telecom, mobile, and internet-access bills are out of
scope and route to a human. Connectivity is a utility, not a software
subscription — an internet-access bill is never 7200. There is no utilities
account in this chart.

## 2. Rent and property leases

Property rent, office and warehouse leases, equipment leases, and parking leases
are out of scope and route to a human. There is no rent or lease account in this
chart.

## 3. Direct statutory and tax payments

Payments made *directly* to a government body — payroll-tax remittance, sales or
VAT remittance, customs duty paid directly, business-licence fees paid to a
regulator, regulatory penalties — are out of scope and route to a human. A
regulatory fee charged *through* a professional adviser is 6500; the direct
payment to the government is not.

## 4. Banking, interest, and financing

Bank fees, card-processing fees, wire fees, interest charges, loan repayments,
and financing costs are out of scope and route to a human. There is no financing
account in this chart.

## 5. Payroll and employee reimbursements

Payroll runs, employee expense reimbursements, per-diems, and benefits payments
are out of scope for this non-PO supplier-invoice policy and route to a human.

## 6. Outbound customer shipping

Shipping goods *to customers* is a cost-of-sale concern outside this non-PO
policy and routes to a human. Only *inbound* freight on purchased goods is
codeable, to 5100.

## 7. Intercompany and internal allocations

Intercompany charges, internal cost allocations, and journal reclasses are not
supplier invoices in the sense this policy governs and route to a human.

## 8. How to abstain correctly

When an invoice matches any clause above, return an empty GL account, cite the
relevant out-of-scope clause as the reason, set low confidence, and route to a
human. Do not attempt a nearest-account guess. Abstaining with a cited reason is
the correct, auditable outcome for an out-of-scope invoice.
