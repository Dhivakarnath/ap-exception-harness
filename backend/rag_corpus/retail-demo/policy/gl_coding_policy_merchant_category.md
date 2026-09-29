# Retail — Merchant-Category to GL Account Mapping

Version: 2.0.0
Effective from: 2026-01-01

This document maps common merchant categories and vendor descriptors to the
seven codeable GL accounts. It exists because a large share of non-PO invoices
carry a recognisable merchant category or line-item descriptor, and mapping that
descriptor to an account is faster and more consistent than re-deriving the
category from first principles each time. A merchant-category match is a valid
citation. Where a category maps to "route to a human", it is out of scope and
must not be forced into an account.

Every mapping resolves to one of: 5000 Raw Materials, 5100 Freight In, 6410
Office Supplies, 6420 Computer Supplies, 6500 Professional Services, 6600
Marketing and Advertising, 7200 Software Subscriptions — or to a human.

## 1. Office and stationery merchants

Office-supply retailers, stationery suppliers, print shops (plain internal
printing), pantry and breakroom suppliers, and janitorial-consumable suppliers
map to 6410 Office Supplies. A print shop producing branded or promotional
material maps instead to 6600.

## 2. Computer and electronics merchants

Computer-hardware retailers, electronics stores, and peripheral suppliers map to
6420 Computer Supplies for hardware, cables, and accessories below threshold. The
same merchant selling a software licence or subscription maps that line to 7200,
and a single device at or above threshold routes to a human.

## 3. Software, SaaS, and cloud vendors

SaaS platforms, cloud-infrastructure providers, developer-tool vendors,
analytics and data-platform vendors, and API providers map to 7200 Software
Subscriptions. A cloud vendor's *managed-service labour* that is separately
invoiced maps to 6500.

## 4. Professional-services firms

Law firms, accounting and audit firms, tax advisers, management consultancies,
contract-engineering and staff-augmentation vendors, recruitment agencies, and
outsourced-operations providers map to 6500 Professional Services. A creative or
marketing agency maps instead to 6600.

## 5. Marketing, advertising, and event merchants

Advertising platforms and networks, media agencies, creative and design studios
(promotional), PR firms, event-production and trade-show fabrication vendors,
influencer and affiliate networks, and promotional-merchandise suppliers map to
6600 Marketing and Advertising. Marketing-technology *software subscriptions* map
to 7200.

## 6. Freight, courier, and logistics merchants

Inbound-freight carriers, couriers, and freight forwarders map to 5100 Freight
In for the movement of purchased goods. Outbound customer shipping is out of
scope and routes to a human; duty and import tax remitted directly to a
government body routes to a human.

## 7. Materials and wholesale merchants

Wholesale materials suppliers and resale-inventory vendors map to 5000 Raw
Materials on the rare non-PO occasion, after confirming there is no missing PO.

## 8. Categories that route to a human

The following merchant categories have no codeable account and route to a human:
utility providers (electricity, water, gas, telecom, internet access); landlords
and property lessors (rent and lease); government bodies and regulators (direct
statutory or tax payments); banks and lenders (fees, interest, financing);
payroll processors and employee reimbursements. A recognisable match to one of
these categories is a reason to abstain, not to force an account.

## 9. When the merchant category is ambiguous

A merchant may sell across categories — an electronics retailer selling both a
monitor (6420) and a software licence (7200), or a full-service agency billing
both media (6600) and a platform subscription (7200). In that case do not code by
the merchant's overall category; code each line by what it is, splitting where
needed. If the line descriptor is too vague to map, abstain.
