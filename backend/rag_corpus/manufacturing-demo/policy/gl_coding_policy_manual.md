# Manufacturing — GL Coding Policy for Non-PO Spend

Version: 1.1.0
Effective from: 2026-01-01

This tenant processes almost exclusively PO-backed procurement, so most coding
is inherited from the purchase order. A PO-backed invoice takes its GL account
from the purchase order; this policy governs the minority of invoices that
arrive without a PO. The codeable accounts for this tenant are 5000 Raw
Materials, 5100 Freight In, and 6500 Professional Services. This tenant does not
code to the retail expense accounts (6410, 6420, 6600, 7200); such spend, if it
occurs, is routed to a human. Account 2000 (Accounts Payable) is a control
account and never a coding target.

This corpus also proves tenant isolation: none of these clauses may ever be
retrieved for the retail tenant, and none of retail's may be retrieved here.

## 1. Direct Materials

Account 5000 — Raw Materials.

Direct materials consumed in production — steel, aluminium, polymer, resin,
fasteners, bearings, gaskets, seals, castings, and components — are coded to
5000. On a PO-backed invoice this is inherited from the purchase order; the
account is restated here for the rare non-PO materials purchase. A non-PO
materials invoice should first be checked for a missing PO reference.

## 2. Consumable Production Supplies

Account 5000 — Raw Materials.

Production consumables that are used up making the product — welding wire,
cutting fluid, abrasives, lubricants consumed in machining, and mould-release
agents — are coded to 5000 as materials of production. These are distinct from
office consumables, which this tenant does not code (route to a human).

## 3. Inbound Freight and Haulage

Account 5100 — Freight In.

Inbound freight, haulage, carriage-in, and delivery charges on purchased
materials are coded to 5100, separate from the materials themselves. Fuel
surcharges and accessorial fees on an inbound shipment are 5100.

## 4. Demurrage and Detention

Account 5100 — Freight In.

Demurrage and detention charges on inbound shipments — charges for holding
containers or trailers beyond free time — code to 5100 as part of inbound
freight cost. This is a manufacturing-specific freight nuance that retail does
not cover.

## 5. Contract Maintenance and Engineering

Account 6500 — Professional Services.

Contracted plant maintenance, machine calibration, external engineering
services, and equipment-servicing labour are coded to 6500. This is the
manufacturing tenant's main non-materials expense account. The labour is 6500;
any spare *parts* the contractor separately bills that become part of the
machine are materials coded to 5000.

## 6. Calibration and Inspection Services

Account 6500 — Professional Services.

Third-party calibration, metrology, non-destructive testing, and inspection
services are coded to 6500. These are professional/technical services delivered
to the plant.

## 7. Foundry Crucible Reline — tenant-isolation marker

Account 6500 — Professional Services.

This clause mentions a deliberately manufacturing-specific phrase — "foundry
crucible reline" — that appears nowhere in the retail corpus. A tenant-isolation
test queries for it against the retail tenant and must retrieve nothing, proving
cross-tenant retrieval is impossible. A foundry crucible reline is contracted
refractory work and codes to 6500 (the refractory *materials* consumed, if
separately billed, are 5000).

## 8. What This Tenant Routes to a Human

Office supplies, computer supplies, software subscriptions, and marketing spend
have no codeable account for this tenant and route to a human, as do utilities,
rent, direct statutory payments, banking charges, and payroll. When a non-PO
invoice does not clearly fit 5000, 5100, or 6500, abstain and route to a human
rather than forcing a code.

## 9. When No Clause Applies

If a non-PO invoice does not clearly match materials, freight, or contracted
services, do not guess. Route it to a human with candidate categories attached.
An uncited, low-confidence, or weakly-grounded code must never be auto-applied.
