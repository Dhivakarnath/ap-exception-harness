# Retail — GL Coding Policy for Non-PO Spend

Version: 2.0.0
Effective from: 2026-01-01

This document governs how accounts payable codes supplier invoices that arrive
without a purchase order. A PO-backed invoice inherits its GL account from the
purchase order; a non-PO invoice has no such inheritance, so the account, cost
center, and entity must be determined from this policy and from prior coded
precedent. Every code applied to a non-PO invoice must cite the clause or
precedent that justifies it, and no code may be auto-applied without a
citation.

The general ledger accounts referenced here are the only codeable accounts in
the chart of accounts: 5000 Raw Materials, 5100 Freight In, 6410 Office
Supplies, 6420 Computer Supplies, 6500 Professional Services, 6600 Marketing
and Advertising, and 7200 Software Subscriptions. Account 2000 (Accounts
Payable) is a control account and is never a coding target. If an invoice does
not clearly belong to one of the codeable accounts, do not guess — route it to
a human.

## 1. Professional and Contract Services

Account 6500 — Professional Services.

Invoices for consulting, legal advice, audit fees, tax-preparation fees,
contract engineering, contract labour, staff-augmentation, and outsourced
professional work are coded to 6500. This includes staff-augmentation and
contract-engineering support billed monthly by a services vendor, management
consulting engagements, external bookkeeping, and recruitment-agency placement
fees. Use 6500 whenever the deliverable is labour or expertise rather than a
physical good or a software licence.

Cost center follows the requesting department. Where the invoice does not name
a department, code to the Operations cost center pending confirmation.

## 2. Legal and Regulatory Fees

Account 6500 — Professional Services.

Law-firm invoices, litigation support, regulatory filing fees charged by a
professional adviser, patent and trademark agent fees, and notary or
commissioning fees are coded to 6500 as professional services. A government
fee paid directly to a regulator (not through an adviser) is not a professional
service and must be routed to a human — there is no dedicated account for
direct statutory payments in this chart.

## 3. Software and SaaS Subscriptions

Account 7200 — Software Subscriptions.

Recurring software-as-a-service fees, platform licence renewals, seat-based
subscriptions, cloud hosting and infrastructure charges (IaaS/PaaS), API usage
fees, analytics- and data-platform charges, and annual maintenance or support
contracts tied to a software product are coded to 7200. A renewal of an
existing analytics or software platform is 7200 even when the vendor also
provides onboarding services, unless the services are separately and materially
invoiced, in which case split the services portion to 6500 (see clause 13).

## 4. Perpetual Software Licences

Account 7200 — Software Subscriptions.

One-time perpetual software licences below the capitalisation threshold of
2,500 are expensed to 7200. A perpetual licence at or above 2,500 may be a
capital asset rather than an expense and must be routed to a human for a
capitalisation decision — this policy does not authorise capitalising an asset
automatically.

## 5. Office Supplies and Consumables

Account 6410 — Office Supplies.

Stationery, paper, printer toner and ink, binders, pens, breakroom and
janitorial consumables, and general office goods bought in bulk are coded to
6410. Toner and paper purchased together on one invoice are a single 6410 line.
Coffee, water, and pantry supplies for the office are 6410.

## 6. Computer Supplies and Small Hardware

Account 6420 — Computer Supplies.

Peripherals, cables, adapters, docking stations, keyboards, mice, monitors,
webcams, headsets, external drives, and small hardware below the 2,500
capitalisation threshold are coded to 6420. Laptop docking stations and similar
accessories are 6420, not 7200 — they are hardware, not software. A single
computer, laptop, or server at or above 2,500 is a capital asset and must be
routed to a human, not expensed to 6420.

## 7. Marketing and Advertising

Account 6600 — Marketing and Advertising.

Campaign production, media buys (print, digital, broadcast, out-of-home),
promotional materials and merchandise, trade-show booth production, event
sponsorship, event build-out, influencer and affiliate fees, public-relations
retainers, and advertising-agency fees are coded to 6600. Trade-show booth
production and event build-out are 6600 even though a construction or
fabrication contractor performs the work, because the spend is promotional in
purpose rather than a professional-services engagement.

## 8. Print and Promotional Production

Account 6600 — Marketing and Advertising.

Branded print runs, catalogues, brochures, signage, banners, and promotional
packaging are coded to 6600 when their purpose is marketing. Plain internal
office printing (letterhead, internal forms) with no promotional purpose is
6410 Office Supplies instead — purpose, not the printing method, decides the
account.

## 9. Direct Materials (rare, non-PO)

Account 5000 — Raw Materials.

On the rare occasion a retail entity buys direct resale or production materials
without a PO, they are coded to 5000. This is uncommon for this tenant; a
materials invoice with no PO should be double-checked for a missing PO
reference before coding.

## 10. Inbound Freight and Delivery

Account 5100 — Freight In.

Inbound freight, courier, and delivery charges on purchased goods are coded to
5100, separate from the goods themselves. Outbound shipping to customers is a
different concern and is not covered by this non-PO policy; route it to a human.
When an invoice bundles goods and inbound freight on one document, split the
freight portion to 5100 and the goods portion to its own account (see clause
12 on split coding).

## 11. Contracted Facilities Labour

Account 6500 — Professional Services.

Cleaning, maintenance, groundskeeping, and repair *labour* performed by a
contracted service provider is coded to 6500 as a professional service. This
covers the labour on the invoice, not any utilities or rent.

## 12. No Account — Route to a Human

The following have NO matching account in this chart and must always be routed
to a human; never force them into any expense account. There is no code to cite
for these, so a coding attempt on one of them must abstain:

- Utility bills: electricity, water, gas, telecoms, internet access.
- Property rent and lease payments.
- Direct statutory or tax payments made to a government body (not through a
  professional adviser): payroll tax remittance, sales/VAT remittance, customs
  duty paid directly, business licence fees paid to a regulator.
- Bank fees, interest, and financing charges.
- Payroll and employee reimbursements.

If an invoice is one of these, return an empty citation list and low
confidence: it is out of scope for automated coding.

## 13. Split Coding Across Accounts

When a single invoice covers items belonging to more than one account — for
example software plus separately-invoiced onboarding services, or goods plus
inbound freight — code each portion to its own account and cite the clause for
each portion. Do not lump a mixed invoice into a single account for
convenience; a split invoice needs a citation per split line.

## 14. When No Clause Applies

If an invoice does not clearly match any category above, do not guess a code.
Route the invoice to a human for coding with the candidate categories attached.
An uncited, low-confidence, or weakly-grounded code must never be auto-applied.
