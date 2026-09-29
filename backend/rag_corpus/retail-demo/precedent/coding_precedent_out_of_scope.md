# Retail — Coded-Invoice Precedent: Out-of-Scope Routing

Version: 2.0.0
Effective from: 2026-01-01

Prior invoices that were correctly routed to a human because they had no
codeable account, retained as precedent for abstention. These entries teach the
system what *not* to code as firmly as the category precedents teach what to
code. Each records the vendor, the line, and that the outcome was route-to-human.

## PowerGrid Utility — electricity bill (route to human)

PowerGrid Utility billed monthly electricity. This was routed to a human as out
of scope — there is no utilities account. An "electricity", "power supply", or
"utility bill" line follows this precedent: route to a human, never 7200.

## CityFibre — internet access (route to human)

CityFibre billed monthly business internet access. This was routed to a human as
a utility, not coded to 7200. An "internet access", "broadband", or
"connectivity" utility line follows this precedent: route to a human.

## Landlord Holdings — office rent (route to human)

Landlord Holdings billed monthly office rent. This was routed to a human — there
is no rent account. A "rent", "lease payment", or "premises" line follows this
precedent: route to a human.

## Revenue Authority — VAT remittance (route to human)

A direct VAT remittance to the Revenue Authority was routed to a human as a
direct statutory payment, not coded to any expense account. A "VAT remittance",
"sales tax due", or "duty payment" made directly to a government body follows
this precedent: route to a human.

## FirstBank — bank fees (route to human)

FirstBank billed account and wire fees. These were routed to a human — there is
no financing account. A "bank fees", "wire charge", or "interest" line follows
this precedent: route to a human.

## Payroll Bureau — payroll run (route to human)

Payroll Bureau's payroll-run charge and employee reimbursements were routed to a
human as out of scope for the non-PO supplier-invoice policy. A "payroll" or
"reimbursement" line follows this precedent: route to a human.
