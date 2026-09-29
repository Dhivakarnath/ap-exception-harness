"""Deterministic seed data.

This is not decorative. Every record exists to make a specific downstream test
possible, so the adversarial dataset (Slice 3) has real counterparties to match
against rather than fabricated ones.

The mapping from seed record to the case it enables is stated inline, because a
seed file whose purpose is undocumented rots into noise.

Fixed IDs and dates: the dataset generators and integration tests assert against
this state, so it must be reproducible byte for byte.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import (
    Account,
    AccountType,
    Bill,
    BillPayment,
    BillStatus,
    EntityRef,
    GoodsReceipt,
    GoodsReceiptLine,
    LineDetailType,
    PurchaseOrder,
    PurchaseOrderStatus,
    TxnLine,
    Vendor,
)

SEED_VERSION = "1.0.0"

# Anchor date. Everything is relative to this so the fixture is stable.
TODAY = date(2026, 3, 2)
USD = EntityRef(value="USD", name="United States Dollar")


def _ref(vendor: Vendor) -> EntityRef:
    return EntityRef(value=vendor.Id, name=vendor.DisplayName)


# --------------------------------------------------------------------- accounts

ACCOUNTS: list[Account] = [
    Account(
        Id="A-1",
        Name="Office Supplies",
        AcctNum="6410",
        AccountType=AccountType.EXPENSE,
        AccountSubType="OfficeGeneralAdministrativeExpenses",
        Description="Stationery, consumables, general office goods.",
    ),
    Account(
        Id="A-2",
        Name="Computer Supplies",
        AcctNum="6420",
        AccountType=AccountType.EXPENSE,
        AccountSubType="OfficeGeneralAdministrativeExpenses",
        Description="Peripherals, cables, small hardware under capitalisation limit.",
    ),
    Account(
        Id="A-3",
        Name="Software Subscriptions",
        AcctNum="7200",
        AccountType=AccountType.EXPENSE,
        AccountSubType="OtherMiscellaneousServiceCost",
        Description="Recurring SaaS and licence fees.",
    ),
    Account(
        Id="A-4",
        Name="Raw Materials",
        AcctNum="5000",
        AccountType=AccountType.COST_OF_GOODS_SOLD,
        AccountSubType="SuppliesMaterialsCogs",
        Description="Direct materials consumed in production.",
    ),
    Account(
        Id="A-5",
        Name="Freight In",
        AcctNum="5100",
        AccountType=AccountType.COST_OF_GOODS_SOLD,
        AccountSubType="ShippingFreightDeliveryCos",
        Description="Inbound freight on purchased materials.",
    ),
    Account(
        Id="A-6",
        Name="Professional Services",
        AcctNum="6500",
        AccountType=AccountType.EXPENSE,
        AccountSubType="LegalProfessionalFees",
        Description="Consulting, legal, audit and contract labour.",
    ),
    Account(
        Id="A-7",
        Name="Marketing and Advertising",
        AcctNum="6600",
        AccountType=AccountType.EXPENSE,
        AccountSubType="AdvertisingPromotional",
        Description="Campaigns, media buys, promotional production.",
    ),
    Account(
        Id="A-8",
        Name="Accounts Payable",
        AcctNum="2000",
        AccountType=AccountType.ACCOUNTS_PAYABLE,
        AccountSubType="AccountsPayable",
        Description="Control account. Never a coding target.",
    ),
]


# --------------------------------------------------------------------- vendors

VENDORS: list[Vendor] = [
    # Primary clean vendor. Most happy-path cases use this one.
    Vendor(
        Id="V-1001",
        DisplayName="Acme Corporation",
        CompanyName="Acme Corporation",
        PrimaryTaxIdentifier="US-42-1234567",
        TermRef=EntityRef(value="T-1", name="Net 30"),
        CurrencyRef=USD,
        BankAccountName="Acme Corporation",
        BankAccountLast4="4821",
        BankRoutingCode="021000021",
        BankName="First National",
        CreatedAt=date(2021, 4, 12),
    ),
    # Deliberately confusable with V-1001 but a genuinely DIFFERENT company.
    # Guards against fuzzy vendor resolution over-merging: a system that folds
    # this into Acme Corporation would pay the wrong party.
    Vendor(
        Id="V-1002",
        DisplayName="Acme Industries LLC",
        CompanyName="Acme Industries LLC",
        PrimaryTaxIdentifier="US-88-7654321",
        TermRef=EntityRef(value="T-1", name="Net 30"),
        CurrencyRef=USD,
        BankAccountName="Acme Industries LLC",
        BankAccountLast4="9903",
        BankRoutingCode="121000358",
        BankName="Pacific Union",
        CreatedAt=date(2022, 9, 30),
    ),
    Vendor(
        Id="V-1003",
        DisplayName="Globex Industries",
        CompanyName="Globex Industries Inc",
        PrimaryTaxIdentifier="US-55-2223334",
        TermRef=EntityRef(value="T-2", name="2/10 Net 30"),
        CurrencyRef=USD,
        BankAccountName="Globex Industries Inc",
        BankAccountLast4="7710",
        BankRoutingCode="026009593",
        BankName="Atlantic Trust",
        CreatedAt=date(2020, 1, 20),
    ),
    # Services vendor with no purchase orders — drives the non-PO / GL-coding
    # branch (FR-5.1, FR-5.2).
    Vendor(
        Id="V-1004",
        DisplayName="Initech Services",
        CompanyName="Initech Services LLC",
        PrimaryTaxIdentifier="US-77-4445556",
        TermRef=EntityRef(value="T-1", name="Net 30"),
        CurrencyRef=USD,
        BankAccountName="Initech Services LLC",
        BankAccountLast4="3312",
        BankRoutingCode="011000015",
        BankName="Commonwealth",
        CreatedAt=date(2023, 6, 5),
    ),
    # Inactive: invoices must not auto-post against a deactivated vendor.
    Vendor(
        Id="V-1005",
        DisplayName="Umbrella Supplies",
        CompanyName="Umbrella Supplies Co",
        Active=False,
        CurrencyRef=USD,
        BankAccountName="Umbrella Supplies Co",
        BankAccountLast4="5150",
        BankName="Midwest Savings",
        CreatedAt=date(2019, 11, 2),
    ),
    # Blocked pending fraud investigation. Distinct from inactive because the
    # reason matters: this must never be paid, at any amount.
    Vendor(
        Id="V-1006",
        DisplayName="Shell Holdings Ltd",
        CompanyName="Shell Holdings Ltd",
        Active=True,
        Blocked=True,
        CurrencyRef=USD,
        BankAccountName="S H Ltd",
        BankAccountLast4="0001",
        BankName="Offshore Commercial",
        CreatedAt=date(2026, 1, 8),
    ),
    # Recently created: a first-time or barely-known vendor is itself an intake
    # fraud signal, independent of the invoice contents.
    Vendor(
        Id="V-1007",
        DisplayName="Northwind Traders",
        CompanyName="Northwind Traders",
        CurrencyRef=USD,
        BankAccountName="Northwind Traders",
        BankAccountLast4="6644",
        BankRoutingCode="122105155",
        BankName="Desert First",
        CreatedAt=date(2026, 2, 24),
    ),
]

_V = {v.Id: v for v in VENDORS}


def _item_line(
    num: int, desc: str, qty: str, unit: str, account: str | None = None
) -> TxnLine:
    quantity = Decimal(qty)
    unit_price = Decimal(unit)
    return TxnLine(
        LineNum=num,
        Description=desc,
        Amount=quantity * unit_price,
        DetailType=LineDetailType.ITEM_BASED,
        Qty=quantity,
        UnitPrice=unit_price,
        UOM="EA",
        AccountRef=EntityRef(value=account) if account else None,
    )


# -------------------------------------------------------------- purchase orders

PURCHASE_ORDERS: list[PurchaseOrder] = [
    # PO-2001 — the clean case. Fully received, small enough to be touchless
    # under the manufacturing pack (ceiling 2500).
    PurchaseOrder(
        Id="PO-1",
        DocNumber="PO-2001",
        VendorRef=_ref(_V["V-1001"]),
        TxnDate=date(2026, 2, 2),
        TotalAmt=Decimal("2400.00"),
        CurrencyRef=USD,
        POStatus=PurchaseOrderStatus.CLOSED,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        DepartmentRef=EntityRef(value="D-1", name="Production"),
        ApprovedBy="buyer@manufacturing.example",
        ApprovedAt=date(2026, 2, 2),
        Line=[
            _item_line(1, "Steel bracket, 40mm", "120", "12.00", "A-4"),
            _item_line(2, "Inbound freight", "1", "960.00", "A-5"),
        ],
    ),
    # PO-2002 — clean but well above the touchless ceiling, so a clean match
    # must still route for approval (FR-4.10).
    PurchaseOrder(
        Id="PO-2",
        DocNumber="PO-2002",
        VendorRef=_ref(_V["V-1001"]),
        TxnDate=date(2026, 2, 5),
        TotalAmt=Decimal("12000.00"),
        CurrencyRef=USD,
        POStatus=PurchaseOrderStatus.CLOSED,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        DepartmentRef=EntityRef(value="D-1", name="Production"),
        ApprovedBy="plant.manager@manufacturing.example",
        ApprovedAt=date(2026, 2, 5),
        Line=[_item_line(1, "Aluminium sheet, 2mm", "400", "30.00", "A-4")],
    ),
    # PO-2003 — partially received. Billing the received portion is legitimate
    # and must not be treated as a mismatch (FR-4.2).
    PurchaseOrder(
        Id="PO-3",
        DocNumber="PO-2003",
        VendorRef=_ref(_V["V-1003"]),
        TxnDate=date(2026, 2, 8),
        TotalAmt=Decimal("5000.00"),
        CurrencyRef=USD,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        ApprovedBy="buyer@manufacturing.example",
        ApprovedAt=date(2026, 2, 8),
        Line=[_item_line(1, "Polymer pellets, 25kg sack", "100", "50.00", "A-4")],
    ),
    # PO-2004 — no goods receipt exists. Under a pack requiring GRN this is a
    # missing-GRN exception, not a pass.
    PurchaseOrder(
        Id="PO-4",
        DocNumber="PO-2004",
        VendorRef=_ref(_V["V-1003"]),
        TxnDate=date(2026, 2, 12),
        TotalAmt=Decimal("3200.00"),
        CurrencyRef=USD,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        ApprovedBy="buyer@manufacturing.example",
        ApprovedAt=date(2026, 2, 12),
        Line=[_item_line(1, "Copper wire spool", "80", "40.00", "A-4")],
    ),
    # PO-2005 — receipt quantity is short of the order. An invoice for the full
    # ordered quantity is overbilling.
    PurchaseOrder(
        Id="PO-5",
        DocNumber="PO-2005",
        VendorRef=_ref(_V["V-1001"]),
        TxnDate=date(2026, 2, 14),
        TotalAmt=Decimal("1800.00"),
        CurrencyRef=USD,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        ApprovedBy="buyer@manufacturing.example",
        ApprovedAt=date(2026, 2, 14),
        Line=[_item_line(1, "Bearing assembly", "60", "30.00", "A-4")],
    ),
    # PO-2006 — sits just under the 5000 DOA boundary for the buyer tier.
    # Enables the threshold-avoidance case (FR-4.8), made sharper by pairing it
    # with the recently-created vendor.
    PurchaseOrder(
        Id="PO-6",
        DocNumber="PO-2006",
        VendorRef=_ref(_V["V-1007"]),
        TxnDate=date(2026, 2, 20),
        TotalAmt=Decimal("4950.00"),
        CurrencyRef=USD,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        ApprovedBy="buyer@manufacturing.example",
        ApprovedAt=date(2026, 2, 20),
        Line=[_item_line(1, "Fastener kit, mixed", "330", "15.00", "A-4")],
    ),
    # PO-2007 / PO-2008 — identical shape, used to test price variance just
    # inside and just outside the 2% manufacturing tolerance.
    PurchaseOrder(
        Id="PO-7",
        DocNumber="PO-2007",
        VendorRef=_ref(_V["V-1001"]),
        TxnDate=date(2026, 2, 22),
        TotalAmt=Decimal("2000.00"),
        CurrencyRef=USD,
        POStatus=PurchaseOrderStatus.CLOSED,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        ApprovedBy="buyer@manufacturing.example",
        ApprovedAt=date(2026, 2, 22),
        Line=[_item_line(1, "Gasket set", "100", "20.00", "A-4")],
    ),
    PurchaseOrder(
        Id="PO-8",
        DocNumber="PO-2008",
        VendorRef=_ref(_V["V-1001"]),
        TxnDate=date(2026, 2, 23),
        TotalAmt=Decimal("2000.00"),
        CurrencyRef=USD,
        POStatus=PurchaseOrderStatus.CLOSED,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        ApprovedBy="buyer@manufacturing.example",
        ApprovedAt=date(2026, 2, 23),
        Line=[_item_line(1, "Seal ring", "200", "10.00", "A-4")],
    ),
]


# -------------------------------------------------------------- goods receipts

GOODS_RECEIPTS: list[GoodsReceipt] = [
    # Full receipt against PO-2001 — the clean three-way match.
    GoodsReceipt(
        Id="GRN-1",
        DocNumber="GRN-3001",
        PurchaseOrderRef=EntityRef(value="PO-1", name="PO-2001"),
        TxnDate=date(2026, 2, 6),
        ReceivedBy="warehouse@manufacturing.example",
        Line=[
            GoodsReceiptLine(LineNum=1, Description="Steel bracket, 40mm", QtyReceived=Decimal("120"), UOM="EA"),
            GoodsReceiptLine(LineNum=2, Description="Inbound freight", QtyReceived=Decimal("1"), UOM="EA"),
        ],
    ),
    GoodsReceipt(
        Id="GRN-2",
        DocNumber="GRN-3002",
        PurchaseOrderRef=EntityRef(value="PO-2", name="PO-2002"),
        TxnDate=date(2026, 2, 10),
        ReceivedBy="warehouse@manufacturing.example",
        Line=[
            GoodsReceiptLine(LineNum=1, Description="Aluminium sheet, 2mm", QtyReceived=Decimal("400"), UOM="EA")
        ],
    ),
    # Partial: 60 of 100 received.
    GoodsReceipt(
        Id="GRN-3",
        DocNumber="GRN-3003",
        PurchaseOrderRef=EntityRef(value="PO-3", name="PO-2003"),
        TxnDate=date(2026, 2, 15),
        ReceivedBy="warehouse@manufacturing.example",
        IsPartial=True,
        Line=[
            GoodsReceiptLine(
                LineNum=1, Description="Polymer pellets, 25kg sack", QtyReceived=Decimal("60"), UOM="EA"
            )
        ],
    ),
    # Deliberately none for PO-2004.
    # Short receipt against PO-2005: 50 of 60.
    GoodsReceipt(
        Id="GRN-5",
        DocNumber="GRN-3005",
        PurchaseOrderRef=EntityRef(value="PO-5", name="PO-2005"),
        TxnDate=date(2026, 2, 18),
        ReceivedBy="warehouse@manufacturing.example",
        IsPartial=True,
        Line=[
            GoodsReceiptLine(LineNum=1, Description="Bearing assembly", QtyReceived=Decimal("50"), UOM="EA")
        ],
    ),
    GoodsReceipt(
        Id="GRN-6",
        DocNumber="GRN-3006",
        PurchaseOrderRef=EntityRef(value="PO-6", name="PO-2006"),
        TxnDate=date(2026, 2, 25),
        ReceivedBy="warehouse@manufacturing.example",
        Line=[
            GoodsReceiptLine(LineNum=1, Description="Fastener kit, mixed", QtyReceived=Decimal("330"), UOM="EA")
        ],
    ),
    GoodsReceipt(
        Id="GRN-7",
        DocNumber="GRN-3007",
        PurchaseOrderRef=EntityRef(value="PO-7", name="PO-2007"),
        TxnDate=date(2026, 2, 26),
        ReceivedBy="warehouse@manufacturing.example",
        Line=[GoodsReceiptLine(LineNum=1, Description="Gasket set", QtyReceived=Decimal("100"), UOM="EA")],
    ),
    GoodsReceipt(
        Id="GRN-8",
        DocNumber="GRN-3008",
        PurchaseOrderRef=EntityRef(value="PO-8", name="PO-2008"),
        TxnDate=date(2026, 2, 27),
        ReceivedBy="warehouse@manufacturing.example",
        Line=[GoodsReceiptLine(LineNum=1, Description="Seal ring", QtyReceived=Decimal("200"), UOM="EA")],
    ),
]


# ------------------------------------------------------- historical bills

# Bill history exists so duplicate detection has something to detect. Without
# it, the duplicate rules would be untestable against a real ledger.
BILLS: list[Bill] = [
    # Exact-duplicate target: an invoice arriving again as INV-77001 for the
    # same vendor and amount is a resubmission.
    Bill(
        Id="B-1",
        DocNumber="INV-77001",
        VendorRef=_ref(_V["V-1001"]),
        TxnDate=date(2026, 1, 12),
        DueDate=date(2026, 2, 11),
        TotalAmt=Decimal("1500.00"),
        Balance=Decimal("0.00"),
        CurrencyRef=USD,
        BillStatus=BillStatus.PAID,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        Line=[_item_line(1, "Steel bracket, 40mm", "125", "12.00", "A-4")],
    ),
    # Fuzzy-duplicate target: INV-88001A, same amount, within days, is a
    # near-miss that exact matching would wave through (FR-4.3).
    Bill(
        Id="B-2",
        DocNumber="INV-88001",
        VendorRef=_ref(_V["V-1003"]),
        TxnDate=date(2026, 2, 3),
        DueDate=date(2026, 3, 5),
        TotalAmt=Decimal("2750.00"),
        Balance=Decimal("0.00"),
        CurrencyRef=USD,
        BillStatus=BillStatus.PAID,
        AccountRef=EntityRef(value="A-4", name="Raw Materials"),
        Line=[_item_line(1, "Polymer pellets, 25kg sack", "55", "50.00", "A-4")],
    ),
    # Non-PO service history. Gives the GL-coding retriever real precedent to
    # cite: "how was this vendor coded before?" (FR-6.1).
    Bill(
        Id="B-3",
        DocNumber="INIT-4410",
        VendorRef=_ref(_V["V-1004"]),
        TxnDate=date(2026, 1, 20),
        DueDate=date(2026, 2, 19),
        TotalAmt=Decimal("4200.00"),
        Balance=Decimal("0.00"),
        CurrencyRef=USD,
        BillStatus=BillStatus.PAID,
        AccountRef=EntityRef(value="A-6", name="Professional Services"),
        PrivateNote="Q1 contract engineering support. Coded to 6500 per policy 5.1.",
        Line=[
            TxnLine(
                LineNum=1,
                Description="Contract engineering, January",
                Amount=Decimal("4200.00"),
                DetailType=LineDetailType.ACCOUNT_BASED,
                AccountRef=EntityRef(value="A-6", name="Professional Services"),
            )
        ],
    ),
    Bill(
        Id="B-4",
        DocNumber="INIT-4455",
        VendorRef=_ref(_V["V-1004"]),
        TxnDate=date(2026, 2, 18),
        DueDate=date(2026, 3, 20),
        TotalAmt=Decimal("4200.00"),
        Balance=Decimal("4200.00"),
        CurrencyRef=USD,
        BillStatus=BillStatus.APPROVED,
        AccountRef=EntityRef(value="A-6", name="Professional Services"),
        PrivateNote="Q1 contract engineering support, February. Coded to 6500.",
        Line=[
            TxnLine(
                LineNum=1,
                Description="Contract engineering, February",
                Amount=Decimal("4200.00"),
                DetailType=LineDetailType.ACCOUNT_BASED,
                AccountRef=EntityRef(value="A-6", name="Professional Services"),
            )
        ],
    ),
    # Software subscription precedent for GL coding.
    Bill(
        Id="B-5",
        DocNumber="GBX-5501",
        VendorRef=_ref(_V["V-1003"]),
        TxnDate=date(2026, 1, 31),
        DueDate=date(2026, 3, 1),
        TotalAmt=Decimal("880.00"),
        Balance=Decimal("0.00"),
        CurrencyRef=USD,
        BillStatus=BillStatus.PAID,
        AccountRef=EntityRef(value="A-3", name="Software Subscriptions"),
        PrivateNote="Annual licence renewal. Coded to 7200.",
        Line=[
            TxnLine(
                LineNum=1,
                Description="Analytics platform licence, annual",
                Amount=Decimal("880.00"),
                DetailType=LineDetailType.ACCOUNT_BASED,
                AccountRef=EntityRef(value="A-3", name="Software Subscriptions"),
            )
        ],
    ),
]


# ------------------------------------------------------- payment history

# Read-only. Present so the ledger looks real; no endpoint creates these
# (ADR-011 — money never moves through this system).
BILL_PAYMENTS: list[BillPayment] = [
    BillPayment(
        Id="BP-1",
        VendorRef=_ref(_V["V-1001"]),
        TxnDate=date(2026, 2, 10),
        TotalAmt=Decimal("1500.00"),
        LinkedTxn=[EntityRef(value="B-1", name="INV-77001")],
    ),
    BillPayment(
        Id="BP-2",
        VendorRef=_ref(_V["V-1003"]),
        TxnDate=date(2026, 3, 1),
        TotalAmt=Decimal("2750.00"),
        LinkedTxn=[EntityRef(value="B-2", name="INV-88001")],
    ),
    BillPayment(
        Id="BP-3",
        VendorRef=_ref(_V["V-1004"]),
        TxnDate=date(2026, 2, 17),
        TotalAmt=Decimal("4200.00"),
        LinkedTxn=[EntityRef(value="B-3", name="INIT-4410")],
    ),
    BillPayment(
        Id="BP-4",
        VendorRef=_ref(_V["V-1003"]),
        TxnDate=date(2026, 2, 28),
        TotalAmt=Decimal("880.00"),
        LinkedTxn=[EntityRef(value="B-5", name="GBX-5501")],
    ),
]
