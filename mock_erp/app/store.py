"""In-memory ledger store with idempotent writes.

In-memory is the right choice for a mock: it resets to a known seed on restart,
which makes demos and integration tests reproducible. Persistence would add a
migration story that proves nothing about the agent.

**Idempotency is the part that matters.** Retrying a failed `post_bill` must not
create a second bill in the ledger. A duplicate payment authorisation caused by
our own retry would be the most embarrassing possible failure of an AP system, so
every write requires an `Idempotency-Key` and a replay returns the original
result unchanged (NFR-7).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app import seed
from app.models import (
    Account,
    Bill,
    BillPayment,
    BillStatus,
    CreateBillRequest,
    CreateExceptionRequest,
    EntityRef,
    ErpException,
    GoodsReceipt,
    HoldBillRequest,
    PurchaseOrder,
    TxnLine,
    Vendor,
)


class ErpConflictError(RuntimeError):
    """A write conflicts with ledger state (e.g. same key, different payload)."""


class ErpNotFoundError(LookupError):
    """A referenced object does not exist."""


class ErpValidationError(ValueError):
    """A write is refused on business grounds."""


@dataclass(slots=True)
class _IdempotencyRecord:
    """A completed write, keyed for replay."""

    request_fingerprint: str
    response: dict[str, Any]
    status_code: int


@dataclass(slots=True)
class LedgerStore:
    """Mutable ledger state. Guarded by a lock so concurrent writes are safe."""

    accounts: dict[str, Account] = field(default_factory=dict)
    vendors: dict[str, Vendor] = field(default_factory=dict)
    purchase_orders: dict[str, PurchaseOrder] = field(default_factory=dict)
    goods_receipts: dict[str, GoodsReceipt] = field(default_factory=dict)
    bills: dict[str, Bill] = field(default_factory=dict)
    payments: dict[str, BillPayment] = field(default_factory=dict)
    exceptions: dict[str, ErpException] = field(default_factory=dict)
    idempotency: dict[str, _IdempotencyRecord] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _sequence: int = 0

    # ------------------------------------------------------------------ setup
    def reset(self) -> None:
        """Restore the deterministic seed state."""
        with self._lock:
            self.accounts = {a.Id: a for a in seed.ACCOUNTS}
            self.vendors = {v.Id: v for v in seed.VENDORS}
            self.purchase_orders = {p.Id: p for p in seed.PURCHASE_ORDERS}
            self.goods_receipts = {g.Id: g for g in seed.GOODS_RECEIPTS}
            self.bills = {b.Id: b for b in seed.BILLS}
            self.payments = {p.Id: p for p in seed.BILL_PAYMENTS}
            self.exceptions = {}
            self.idempotency = {}
            self._sequence = 0

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{1000 + self._sequence}"

    # ------------------------------------------------------------------ reads
    def list_accounts(self) -> list[Account]:
        return sorted(self.accounts.values(), key=lambda a: a.AcctNum)

    def get_account(self, account_id: str) -> Account:
        try:
            return self.accounts[account_id]
        except KeyError as exc:
            raise ErpNotFoundError(f"Account {account_id!r} not found") from exc

    def list_vendors(self, *, name_contains: str | None = None) -> list[Vendor]:
        vendors = sorted(self.vendors.values(), key=lambda v: v.DisplayName)
        if name_contains:
            needle = name_contains.casefold()
            vendors = [
                v
                for v in vendors
                if needle in v.DisplayName.casefold()
                or (v.CompanyName and needle in v.CompanyName.casefold())
            ]
        return vendors

    def get_vendor(self, vendor_id: str) -> Vendor:
        try:
            return self.vendors[vendor_id]
        except KeyError as exc:
            raise ErpNotFoundError(f"Vendor {vendor_id!r} not found") from exc

    def get_purchase_order(self, doc_number: str) -> PurchaseOrder:
        for po in self.purchase_orders.values():
            if po.DocNumber == doc_number:
                return po
        raise ErpNotFoundError(f"PurchaseOrder {doc_number!r} not found")

    def list_goods_receipts(self, *, po_doc_number: str) -> list[GoodsReceipt]:
        """Receipts for a PO.

        An empty list is a legitimate answer, not an error: "nothing has been
        received yet" is exactly the missing-GRN case the match must detect.
        """
        po = self.get_purchase_order(po_doc_number)
        return [
            g for g in self.goods_receipts.values() if g.PurchaseOrderRef.value == po.Id
        ]

    def list_bills(
        self,
        *,
        vendor_id: str | None = None,
        doc_number: str | None = None,
        since: date | None = None,
    ) -> list[Bill]:
        """Bill history. Backs duplicate detection (FR-4.3)."""
        bills = list(self.bills.values())
        if vendor_id:
            bills = [b for b in bills if b.VendorRef.value == vendor_id]
        if doc_number:
            bills = [b for b in bills if b.DocNumber == doc_number]
        if since:
            bills = [b for b in bills if b.TxnDate >= since]
        return sorted(bills, key=lambda b: (b.TxnDate, b.DocNumber), reverse=True)

    def get_bill(self, bill_id: str) -> Bill:
        try:
            return self.bills[bill_id]
        except KeyError as exc:
            raise ErpNotFoundError(f"Bill {bill_id!r} not found") from exc

    def list_payments(self, *, vendor_id: str | None = None) -> list[BillPayment]:
        payments = list(self.payments.values())
        if vendor_id:
            payments = [p for p in payments if p.VendorRef.value == vendor_id]
        return sorted(payments, key=lambda p: p.TxnDate, reverse=True)

    def list_exceptions(self, *, unresolved_only: bool = False) -> list[ErpException]:
        items = list(self.exceptions.values())
        if unresolved_only:
            items = [e for e in items if not e.Resolved]
        return sorted(items, key=lambda e: e.Id)

    # ----------------------------------------------------------- idempotency
    def check_idempotency(
        self, key: str, fingerprint: str
    ) -> _IdempotencyRecord | None:
        """Return a prior result for this key, or None if unseen.

        A repeat with a *different* payload is a conflict rather than a silent
        overwrite: it means the caller reused a key for a different intent, which
        is a bug worth surfacing.
        """
        with self._lock:
            record = self.idempotency.get(key)
            if record is None:
                return None
            if record.request_fingerprint != fingerprint:
                raise ErpConflictError(
                    f"Idempotency-Key {key!r} was already used with a different "
                    "payload. Reusing a key for a different request would make "
                    "the ledger ambiguous."
                )
            return record

    def _remember(
        self, key: str, fingerprint: str, response: dict[str, Any], status_code: int
    ) -> None:
        self.idempotency[key] = _IdempotencyRecord(
            request_fingerprint=fingerprint,
            response=response,
            status_code=status_code,
        )

    # ----------------------------------------------------------------- writes
    def create_bill(
        self, req: CreateBillRequest, *, idempotency_key: str, fingerprint: str
    ) -> tuple[Bill, bool]:
        """Post a bill. Returns (bill, was_replay)."""
        with self._lock:
            existing = self.check_idempotency(idempotency_key, fingerprint)
            if existing is not None:
                return Bill.model_validate(existing.response), True

            vendor = self.get_vendor(req.VendorId)

            # Business refusals. These are the ERP's own guardrails, independent
            # of anything the agent believes — the second layer of defence in
            # depth at the system-of-action boundary.
            if vendor.Blocked:
                raise ErpValidationError(
                    f"Vendor {vendor.DisplayName!r} is blocked. Refusing to post a bill."
                )
            if not vendor.Active:
                raise ErpValidationError(
                    f"Vendor {vendor.DisplayName!r} is inactive. Refusing to post a bill."
                )

            linked: list[EntityRef] = []
            account_ref: EntityRef | None = None

            if req.PurchaseOrderDocNumber:
                po = self.get_purchase_order(req.PurchaseOrderDocNumber)
                if po.VendorRef.value != vendor.Id:
                    raise ErpValidationError(
                        f"PurchaseOrder {po.DocNumber} belongs to vendor "
                        f"{po.VendorRef.name!r}, not {vendor.DisplayName!r}."
                    )
                linked.append(EntityRef(value=po.Id, name=po.DocNumber))

            if req.GoodsReceiptDocNumber:
                grn = next(
                    (
                        g
                        for g in self.goods_receipts.values()
                        if g.DocNumber == req.GoodsReceiptDocNumber
                    ),
                    None,
                )
                if grn is None:
                    raise ErpNotFoundError(
                        f"GoodsReceipt {req.GoodsReceiptDocNumber!r} not found"
                    )
                linked.append(EntityRef(value=grn.Id, name=grn.DocNumber))

            if req.AccountId:
                account = self.get_account(req.AccountId)
                if account.AccountType == "Accounts Payable":
                    raise ErpValidationError(
                        "Accounts Payable is a control account and is not a valid "
                        "coding target."
                    )
                account_ref = EntityRef(value=account.Id, name=account.Name)

            # A bill created here is at most Approved-for-payment. PAID exists
            # only in seeded history (ADR-011).
            if req.Status is BillStatus.PAID:
                raise ErpValidationError(
                    "Cannot create a bill in PAID status. This system does not "
                    "execute payments."
                )

            bill = Bill(
                Id=self._next_id("B"),
                DocNumber=req.DocNumber,
                VendorRef=EntityRef(value=vendor.Id, name=vendor.DisplayName),
                TxnDate=req.TxnDate,
                DueDate=req.DueDate,
                TotalAmt=req.TotalAmt,
                Balance=req.TotalAmt,
                CurrencyRef=EntityRef(value=req.CurrencyCode),
                BillStatus=req.Status,
                AccountRef=account_ref,
                ClassRef=EntityRef(value=req.ClassName) if req.ClassName else None,
                DepartmentRef=(
                    EntityRef(value=req.DepartmentName) if req.DepartmentName else None
                ),
                PrivateNote=req.PrivateNote,
                LinkedTxn=linked,
                Line=[
                    TxnLine(
                        LineNum=line.LineNum,
                        Description=line.Description,
                        Amount=line.Amount,
                        DetailType=line.DetailType,
                        AccountRef=line.AccountRef,
                        ItemRef=line.ItemRef,
                        Qty=line.Qty,
                        UnitPrice=line.UnitPrice,
                        UOM=line.UOM,
                    )
                    for line in req.Line
                ],
            )
            self.bills[bill.Id] = bill
            self._remember(
                idempotency_key, fingerprint, bill.model_dump(mode="json"), 201
            )
            return bill, False

    def hold_bill(
        self,
        bill_id: str,
        req: HoldBillRequest,
        *,
        idempotency_key: str,
        fingerprint: str,
    ) -> tuple[Bill, bool]:
        """Place a bill on hold."""
        with self._lock:
            existing = self.check_idempotency(idempotency_key, fingerprint)
            if existing is not None:
                return Bill.model_validate(existing.response), True

            bill = self.get_bill(bill_id)
            if bill.BillStatus is BillStatus.PAID:
                raise ErpValidationError(
                    f"Bill {bill.DocNumber} is already paid and cannot be held."
                )

            held = bill.model_copy(
                update={
                    "BillStatus": BillStatus.ON_HOLD,
                    "HoldReason": f"{req.Reason} (raised by {req.RaisedBy})",
                }
            )
            self.bills[held.Id] = held
            self._remember(
                idempotency_key, fingerprint, held.model_dump(mode="json"), 200
            )
            return held, False

    def create_exception(
        self,
        req: CreateExceptionRequest,
        *,
        idempotency_key: str,
        fingerprint: str,
        txn_date: date,
    ) -> tuple[ErpException, bool]:
        """Raise an exception into the AP exception queue."""
        with self._lock:
            existing = self.check_idempotency(idempotency_key, fingerprint)
            if existing is not None:
                return ErpException.model_validate(existing.response), True

            vendor_ref: EntityRef | None = None
            if req.VendorId:
                vendor = self.get_vendor(req.VendorId)
                vendor_ref = EntityRef(value=vendor.Id, name=vendor.DisplayName)

            po_ref: EntityRef | None = None
            if req.PurchaseOrderDocNumber:
                po = self.get_purchase_order(req.PurchaseOrderDocNumber)
                po_ref = EntityRef(value=po.Id, name=po.DocNumber)

            record = ErpException(
                Id=self._next_id("EXC"),
                Kind=req.Kind,
                VendorRef=vendor_ref,
                InvoiceNumber=req.InvoiceNumber,
                PurchaseOrderRef=po_ref,
                Amount=req.Amount,
                Detail=req.Detail,
                RaisedBy=req.RaisedBy,
                TxnDate=txn_date,
            )
            self.exceptions[record.Id] = record
            self._remember(
                idempotency_key, fingerprint, record.model_dump(mode="json"), 201
            )
            return record, False

    # ------------------------------------------------------------------ stats
    def counts(self) -> dict[str, int]:
        return {
            "accounts": len(self.accounts),
            "vendors": len(self.vendors),
            "purchase_orders": len(self.purchase_orders),
            "goods_receipts": len(self.goods_receipts),
            "bills": len(self.bills),
            "payments": len(self.payments),
            "exceptions": len(self.exceptions),
        }


def fingerprint_payload(payload: dict[str, Any]) -> str:
    """Stable fingerprint of a request body, for idempotency comparison."""
    import hashlib
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decimal_str(value: Decimal) -> str:
    """Render a Decimal without scientific notation."""
    return format(value, "f")


# Module-level singleton, seeded at import.
store = LedgerStore()
store.reset()
