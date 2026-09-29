# Retail — Chart of Accounts Guide for Expense Coding

Version: 2.0.0
Effective from: 2026-01-01

This guide is the authoritative reference for what each codeable general-ledger
account means, what belongs in it, and — just as importantly — what does not.
It exists because the single most common coding error is not a wrong category
but a plausible-looking wrong account: two accounts that both "could" fit, and
the coder picks the one that is easier to identify rather than the one that is
correct. Each account below has a definition, an inclusion list, an exclusion
list, and the neighbouring account it is most often confused with.

The chart has exactly seven codeable expense accounts. There are no others. If a
line does not belong in one of these seven, it is out of scope for automated
coding and must be routed to a human. Do not invent an account, do not stretch a
definition to force a fit, and do not default to a catch-all — this chart has no
catch-all by design, because a catch-all is where coding accuracy goes to die.

Account 2000 (Accounts Payable) is a control account. It is the liability that
every purchase invoice credits; it is never the expense account an invoice line
is coded to. Any suggestion to "code to 2000" is a category error.

## Account 5000 — Raw Materials

Direct materials that enter a product or are held for resale. In a retail
entity this is uncommon on non-PO invoices, because materials are normally
bought against a purchase order that already carries the account. Include here:
resale inventory bought without a PO, production or assembly materials, and
components that become part of a finished good. Exclude: office consumables
(6410), computer peripherals (6420), and anything that is a service rather than
a good. The account most often confused with 5000 is 6410 — the test is whether
the item becomes part of a product or resale stock (5000) or is consumed running
the office (6410).

## Account 5100 — Freight In

Inbound freight, carriage, courier, and delivery charges on purchased goods.
This is the cost of getting purchased goods to the business, booked separately
from the goods themselves. Include: inbound courier and freight, carriage-in,
customs brokerage handling billed by the freight forwarder, fuel surcharges and
accessorial fees on an inbound shipment. Exclude: outbound shipping to
customers (out of scope for this non-PO policy), and duty or tax remitted
directly to a government body (out of scope). The account most often confused
with 5100 is 5000 — freight is the movement cost, not the goods; when a single
invoice bundles both, split them.

## Account 6410 — Office Supplies

General office consumables and low-value office goods. Include: paper,
stationery, printer toner and ink, binders, pens, labels, envelopes, breakroom
and pantry supplies (coffee, water, snacks), janitorial and cleaning
consumables, first-aid and facility consumables, and plain internal printing
with no promotional purpose. Exclude: computer peripherals and small hardware
(6420), branded or promotional print (6600), and any single item at or above
the capitalisation threshold. The account most often confused with 6410 is 6420
— paper and toner are 6410; a cable, adapter, or keyboard is 6420. The other
frequent confusion is 6600: a print run is 6410 only when it is plain and
internal; the moment it is branded or promotional it is 6600.

## Account 6420 — Computer Supplies

Computer peripherals, accessories, and small hardware below the capitalisation
threshold. Include: cables, adapters, docking stations, keyboards, mice,
monitors, webcams, headsets, external drives, USB hubs, laptop stands, and
small networking gear that is expensed rather than capitalised. Exclude:
software and subscriptions (7200), plain office consumables (6410), and any
single computer, laptop, server, or hardware item at or above the
capitalisation threshold — that is a capital-asset decision for a human, not an
expense to 6420. The account most often confused with 6420 is 7200 — a docking
station or monitor is hardware (6420); a software licence or SaaS seat is 7200,
even when sold by the same vendor on the same invoice.

## Account 6500 — Professional Services

Labour and expertise delivered by an external provider. Include: consulting,
legal advice, audit and tax-preparation fees, contract engineering and contract
labour, staff augmentation, outsourced bookkeeping, recruitment placement fees,
notary and commissioning fees, and contracted facilities labour (cleaning,
maintenance, repair labour). Exclude: software subscriptions even when a service
component exists (7200 unless the services are separately and materially
invoiced), marketing and creative agency work whose purpose is promotion (6600),
and direct statutory payments to a government body (out of scope). The account
most often confused with 6500 is 6600 — the test is purpose: expertise or labour
delivered to the business is 6500; work whose purpose is to promote the business
is 6600, even if a contractor performs it.

## Account 6600 — Marketing and Advertising

Spend whose purpose is to promote the business. Include: campaign production,
media buys across print, digital, broadcast and out-of-home, promotional
materials and branded merchandise, trade-show booth production and event
build-out, event sponsorship, influencer and affiliate fees, public-relations
retainers, and advertising-agency fees. Exclude: professional-services labour
with no promotional purpose (6500), and plain internal printing (6410). The
account most often confused with 6600 is 6500 — a design or production vendor
doing promotional work is 6600 even though it is "a service"; purpose governs,
not the vendor's industry.

## Account 7200 — Software Subscriptions

Recurring and licence-based software spend. Include: software-as-a-service fees,
platform and seat-based licence renewals, cloud hosting and infrastructure
(IaaS/PaaS), API usage fees, analytics- and data-platform charges, and annual
maintenance or support tied to a software product. Also include one-time
perpetual software licences below the capitalisation threshold. Exclude:
computer hardware and peripherals (6420), professional-services onboarding that
is separately and materially invoiced (6500), and any perpetual licence at or
above the capitalisation threshold — that is a capitalisation decision for a
human. The account most often confused with 7200 is 6420 — the software is 7200;
the device it runs on is 6420.

## How to choose between two plausible accounts

When two accounts both seem to fit, apply these tie-breakers in order. First,
good versus service: a physical good is 5000/5100/6410/6420 depending on type; a
service is 6500/6600/7200 depending on purpose. Second, for services, purpose:
promotion is 6600, software is 7200, everything else labour or expertise is
6500. Third, for goods, function: resale or production input is 5000, movement
cost is 5100, computer hardware is 6420, everything else office is 6410. Fourth,
threshold: any single capitalisable item at or above the threshold is a human
decision, never an automatic expense. If after all four tie-breakers the account
is still unclear, abstain and route to a human — an unsure code is worse than no
code, because a wrong code that looks confident is the hardest error to catch at
month-end close.
