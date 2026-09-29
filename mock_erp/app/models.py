"""ERP object model, shaped like QuickBooks Online.

Field names deliberately follow QBO conventions (`DocNumber`, `VendorRef`,
`TotalAmt`, `TxnDate`, `Line`) rather than our canonical names. Two reasons:

1. **Recognisability.** QuickBooks runs AP for millions of SMBs. A
   finance-literate reviewer reads `VendorRef` / `TotalAmt` and immediately knows
   what this is.
2. **The mapping layer becomes real work.** If the mock used our canonical field
   names, the connector's canonical→ERP mapping (FR-3.4) would be a no-op and
   the seam we claim to have designed would be untested. Using foreign names
   forces the mapping to exist and be exercised.

Simplification, recorded deliberately: this is a REST resource API with QBO
*object shapes*, not a reproduction of the QBO API surface (no `realmId`
routing, no SQL-ish `/query` endpoint, no OAuth dance). Those are integration
plumbing that would prove nothing about the agent. See ADR-014.

**No payment execution exists in this model.** There is a `BillPayment` read
shape so history is realistic, but no endpoint creates one. The agent terminates
at "approved for payment" (ADR-011).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# Monetary values are Decimal end to end. JSON serialisation emits them as
# strings, which is what we want: a float round-trip would reintroduce the
# rounding error the domain layer forbids.
_MoneyField = Field(..., description="Exact amount; serialised as a string.")


class EntityRef(BaseModel):
    """QBO reference object: `{"value": "42", "name": "Acme Corporation"}`."""

    model_config = ConfigDict(frozen=True)

    value: str
    name: str | None = None


# ------------------------------------------------------------------- accounts


class AccountType(StrEnum):
    EXPENSE = "Expense"
    COST_OF_GOODS_SOLD = "Cost of Goods Sold"
    FIXED_ASSET = "Fixed Asset"
    ACCOUNTS_PAYABLE = "Accounts Payable"
    OTHER_CURRENT_LIABILITY = "Other Current Liability"


class Account(BaseModel):
    """Chart-of-accounts entry. The target of GL coding."""

    model_config = ConfigDict(frozen=True)

    Id: str
    Name: str
    AcctNum: str
    AccountType: AccountType
    AccountSubType: str | None = None
    Active: bool = True
    Description: str | None = None


# -------------------------------------------------------------------- vendors


class Vendor(BaseModel):
    """Supplier master record."""

    model_config = ConfigDict(frozen=True)

    Id: str
    DisplayName: str
    CompanyName: str | None = None
    Active: bool = True
    # QBO has no "blocked" flag; a real deployment models it with a custom
    # field. Represented explicitly here because a blocked vendor must never be
    # auto-paid, and conflating it with Active=false would lose the reason.
    Blocked: bool = False
    PrimaryTaxIdentifier: str | None = None
    TermRef: EntityRef | None = None
    CurrencyRef: EntityRef | None = None

    # Remit-to details. Only last-four fragments are exposed: enough for the
    # bank-detail-change check (FR-4.6), not enough to be worth stealing.
    BankAccountName: str | None = None
    BankAccountLast4: str | None = Field(default=None, max_length=4)
    BankRoutingCode: str | None = None
    BankName: str | None = None

    CreatedAt: date | None = None


# ------------------------------------------------------------- lines / txns


class LineDetailType(StrEnum):
    ITEM_BASED = "ItemBasedExpenseLineDetail"
    ACCOUNT_BASED = "AccountBasedExpenseLineDetail"


class TxnLine(BaseModel):
    """A transaction line.

    QBO splits line detail across `ItemBasedExpenseLineDetail` (goods, carries
    quantity and unit price) and `AccountBasedExpenseLineDetail` (services,
    amount only). Flattened here to one shape with optional quantity fields,
    which is a simplification the connector absorbs.
    """

    model_config = ConfigDict(frozen=True)

    LineNum: int = Field(ge=1)
    Description: str
    Amount: Decimal = _MoneyField
    DetailType: LineDetailType = LineDetailType.ITEM_BASED
    ItemRef: EntityRef | None = None
    AccountRef: EntityRef | None = None
    ClassRef: EntityRef | None = None
    Qty: Decimal | None = None
    UnitPrice: Decimal | None = None
    UOM: str | None = None


class PurchaseOrderStatus(StrEnum):
    OPEN = "Open"
    CLOSED = "Closed"
    CANCELLED = "Cancelled"


class PurchaseOrder(BaseModel):
    """What was ordered — and therefore what a human already authorised.

    A PO-backed invoice inherits coding and approval path from here, which is
    what makes touchless approval of a clean match defensible (FR-11.4).
    """

    model_config = ConfigDict(frozen=True)

    Id: str
    DocNumber: str
    VendorRef: EntityRef
    TxnDate: date
    TotalAmt: Decimal = _MoneyField
    CurrencyRef: EntityRef
    POStatus: PurchaseOrderStatus = PurchaseOrderStatus.OPEN
    Line: list[TxnLine] = Field(default_factory=list)

    # Coding and authority attributes inherited by a matched invoice.
    AccountRef: EntityRef | None = None
    ClassRef: EntityRef | None = None
    DepartmentRef: EntityRef | None = None
    ProjectRef: EntityRef | None = None
    ApprovedBy: str | None = None
    ApprovedAt: date | None = None


class GoodsReceiptLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    LineNum: int = Field(ge=1)
    Description: str
    ItemRef: EntityRef | None = None
    QtyReceived: Decimal
    UOM: str | None = None


class GoodsReceipt(BaseModel):
    """What was actually received — the third leg of the three-way match.

    QBO does not model goods receipts natively (it is an inventory concept that
    lives in NetSuite/SAP). Included because three-way matching is the control
    this project implements, and a two-way-only mock could not exercise it.
    """

    model_config = ConfigDict(frozen=True)

    Id: str
    DocNumber: str
    PurchaseOrderRef: EntityRef
    TxnDate: date
    ReceivedBy: str | None = None
    IsPartial: bool = False
    Line: list[GoodsReceiptLine] = Field(default_factory=list)


class BillStatus(StrEnum):
    OPEN = "Open"
    ON_HOLD = "OnHold"
    APPROVED = "Approved"
    """Approved for payment. Terminal state for this system (ADR-011)."""
    PAID = "Paid"
    """Only ever set by seed data representing history. No endpoint produces it."""
    VOID = "Void"


class Bill(BaseModel):
    """A vendor bill in the ledger.

    Posting a Bill is the primary write this system performs. `PrivateNote`
    carries the agent's rationale so the reasoning survives in the ERP itself,
    not only in our platform.
    """

    model_config = ConfigDict(frozen=True)

    Id: str
    DocNumber: str
    """The vendor's invoice number. Duplicate detection keys on this."""
    VendorRef: EntityRef
    TxnDate: date
    DueDate: date | None = None
    TotalAmt: Decimal = _MoneyField
    Balance: Decimal | None = None
    CurrencyRef: EntityRef
    BillStatus: BillStatus = BillStatus.OPEN
    SalesTermRef: EntityRef | None = None
    Line: list[TxnLine] = Field(default_factory=list)
    LinkedTxn: list[EntityRef] = Field(default_factory=list)
    """Links to the PO and GRN this bill was matched against."""
    AccountRef: EntityRef | None = None
    ClassRef: EntityRef | None = None
    DepartmentRef: EntityRef | None = None
    PrivateNote: str | None = None
    HoldReason: str | None = None


class BillPayment(BaseModel):
    """Read-only payment history.

    Present so seeded history looks like a real ledger. **No endpoint creates
    one** — money never moves through this system (ADR-011).
    """

    model_config = ConfigDict(frozen=True)

    Id: str
    VendorRef: EntityRef
    TxnDate: date
    TotalAmt: Decimal = _MoneyField
    PayType: str = "Check"
    LinkedTxn: list[EntityRef] = Field(default_factory=list)


# ------------------------------------------------------------------ exceptions


class ExceptionKind(StrEnum):
    """Why an invoice could not be posted cleanly."""

    MATCH_FAILURE = "MatchFailure"
    MISSING_GRN = "MissingGoodsReceipt"
    DUPLICATE_SUSPECTED = "DuplicateSuspected"
    VENDOR_BLOCKED = "VendorBlocked"
    BANK_DETAIL_CHANGE = "BankDetailChange"
    DATA_QUALITY = "DataQuality"
    OTHER = "Other"


class ErpException(BaseModel):
    """An exception record raised against an invoice.

    Mirrors the "exception queue" that AP teams actually work from.
    """

    model_config = ConfigDict(frozen=True)

    Id: str
    Kind: ExceptionKind
    VendorRef: EntityRef | None = None
    InvoiceNumber: str | None = None
    PurchaseOrderRef: EntityRef | None = None
    Amount: Decimal | None = None
    Detail: str
    RaisedBy: str
    TxnDate: date
    Resolved: bool = False


# ------------------------------------------------------------ write payloads


class BillLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    LineNum: int = Field(ge=1)
    Description: str
    Amount: Decimal
    DetailType: LineDetailType = LineDetailType.ITEM_BASED
    AccountRef: EntityRef | None = None
    ItemRef: EntityRef | None = None
    Qty: Decimal | None = None
    UnitPrice: Decimal | None = None
    UOM: str | None = None


class CreateBillRequest(BaseModel):
    """Post a bill. `extra="forbid"` so a typo'd field is a 422, not a silent drop."""

    model_config = ConfigDict(extra="forbid")

    DocNumber: str = Field(min_length=1, max_length=128)
    VendorId: str = Field(min_length=1)
    TxnDate: date
    DueDate: date | None = None
    TotalAmt: Decimal = Field(gt=0, description="Must be positive; a zero or negative bill is a data defect.")
    CurrencyCode: str = Field(min_length=3, max_length=3)
    Status: BillStatus = BillStatus.APPROVED
    AccountId: str | None = None
    ClassName: str | None = None
    DepartmentName: str | None = None
    PurchaseOrderDocNumber: str | None = None
    GoodsReceiptDocNumber: str | None = None
    PrivateNote: str | None = Field(
        default=None,
        max_length=4000,
        description="Agent rationale, so the reasoning survives in the ERP.",
    )
    Line: list[BillLineInput] = Field(default_factory=list)


class HoldBillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    Reason: str = Field(min_length=1, max_length=1000)
    RaisedBy: str = Field(min_length=1)


class CreateExceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    Kind: ExceptionKind
    Detail: str = Field(min_length=1, max_length=4000)
    RaisedBy: str = Field(min_length=1)
    VendorId: str | None = None
    InvoiceNumber: str | None = None
    PurchaseOrderDocNumber: str | None = None
    Amount: Decimal | None = None


# ------------------------------------------------------------------ responses


class Health(BaseModel):
    status: str
    service: str
    object_model: list[str]
    supports_payment_execution: bool
    seed_version: str
    counts: dict[str, int]


class QueryResponse[T](BaseModel):
    """QBO wraps list results in a `QueryResponse` envelope."""

    QueryResponse: list[T]
    totalCount: int
