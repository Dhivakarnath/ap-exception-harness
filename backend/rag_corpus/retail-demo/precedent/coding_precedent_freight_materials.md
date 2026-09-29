# Retail — Coded-Invoice Precedent: Freight and Materials

Version: 2.0.0
Effective from: 2026-01-01

Prior freight and materials invoices coded and approved by a human, retained as
precedent for accounts 5100 (Freight In) and 5000 (Raw Materials). These are
uncommon on non-PO retail invoices, so each entry also records the missing-PO
check where relevant.

## SwiftFreight — inbound carriage

SwiftFreight bills inbound carriage and courier charges on purchased goods.
These have been coded to 5100 (Freight In). An "inbound freight", "carriage-in",
or "courier delivery" line on purchased goods follows this precedent: 5100.

## SwiftFreight — customs handling (5100) vs duty (route to human)

On a SwiftFreight import invoice, the "customs brokerage handling" fee was coded
to 5100, while a separate "import duty" line remitted to the government was
routed to a human. This split follows precedent: forwarder handling 5100, direct
duty route to a human.

## GoodsWholesale — resale materials without PO

GoodsWholesale billed resale inventory with no PO. After confirming there was no
missing PO, the materials were coded to 5000 (Raw Materials). A confirmed non-PO
"resale materials" line follows this precedent: 5000 — but only after the
missing-PO check.

## GoodsWholesale — goods plus freight (split)

A GoodsWholesale invoice listed goods and inbound freight separately. The goods
were coded to 5000 and the freight to 5100. A bundled goods-plus-freight invoice
follows this precedent: split, goods 5000, freight 5100.

## ShipOut — outbound customer shipping (routed to human)

ShipOut billed for outbound shipping to customers. This was routed to a human as
out of scope for the non-PO coding policy, not coded to 5100. An outbound
customer-shipping line follows this precedent: route to a human.
