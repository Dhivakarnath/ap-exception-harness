"""Mock ERP ledger — HTTP surface.

Stands in for the customer's accounting system. The object model mirrors
QuickBooks Online so swapping in a real QBO/ERPNext backend is a connector
change rather than a redesign (ADR-014).

Two deliberate properties:

**No payment execution endpoint exists.** Not "disabled", not "guarded" —
absent. The agent terminates at "approved for payment" and the payment run stays
a human action inside the real ERP (ADR-011). `/health` asserts this so a
reviewer can verify the boundary without reading the code.

**Writes require an Idempotency-Key.** A retried post must not create a second
bill (NFR-7). Replays return the original result; key reuse with a different
payload is a 409 rather than a silent overwrite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any

from fastapi import FastAPI, Header, Query, Response, status
from fastapi.responses import JSONResponse

from app.models import (
    Account,
    Bill,
    BillPayment,
    CreateBillRequest,
    CreateExceptionRequest,
    ErpException,
    GoodsReceipt,
    Health,
    HoldBillRequest,
    PurchaseOrder,
    Vendor,
)
from app.seed import SEED_VERSION, TODAY
from app.store import (
    ErpConflictError,
    ErpNotFoundError,
    ErpValidationError,
    fingerprint_payload,
    store,
)

_FORBIDDEN_PATH_FRAGMENTS = ("/pay", "/payments/execute", "/disburse", "/remit")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Assert the payment-execution boundary at startup.

    The absence of a payment capability is an architectural commitment
    (ADR-011), so it is verified mechanically rather than left to code review.
    If someone later adds a route that moves money, the service refuses to boot.
    """
    for route in application.routes:
        path: str = getattr(route, "path", "")
        methods: set[str] = getattr(route, "methods", set()) or set()
        if any(frag in path for frag in _FORBIDDEN_PATH_FRAGMENTS) and methods & {
            "POST",
            "PUT",
            "PATCH",
        }:
            raise RuntimeError(
                f"Payment-execution route detected: {sorted(methods)} {path}. "
                "This system must not move money (ADR-011)."
            )
    yield


app = FastAPI(
    title="Mock ERP Ledger",
    version="1.0.0",
    description=(
        "QuickBooks-Online-shaped ledger used as the system of action. "
        "Read PO/GRN/vendor/bill history; write bills, holds, and exceptions. "
        "Contains no payment-execution endpoint by design."
    ),
    lifespan=lifespan,
)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        description="Required on writes. Replays return the original result.",
    ),
]


# ---------------------------------------------------------------- error mapping


@app.exception_handler(ErpNotFoundError)
async def _not_found(_: Any, exc: ErpNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"Fault": {"Message": str(exc)}})


@app.exception_handler(ErpValidationError)
async def _refused(_: Any, exc: ErpValidationError) -> JSONResponse:
    # 422: the request was understood but the ledger refuses it on business
    # grounds. Distinct from 400 so the caller can tell a malformed request from
    # a legitimately refused one.
    return JSONResponse(status_code=422, content={"Fault": {"Message": str(exc)}})


@app.exception_handler(ErpConflictError)
async def _conflict(_: Any, exc: ErpConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"Fault": {"Message": str(exc)}})


# ----------------------------------------------------------------------- health


@app.get("/health", response_model=Health, tags=["ops"])
def health() -> Health:
    return Health(
        status="ok",
        service="mock-erp",
        object_model=[
            "Account",
            "Vendor",
            "PurchaseOrder",
            "GoodsReceipt",
            "Bill",
            "BillPayment",
            "ErpException",
        ],
        # Always False, asserted in tests. Money never moves through this system.
        supports_payment_execution=False,
        seed_version=SEED_VERSION,
        counts=store.counts(),
    )


@app.post("/admin/reset", status_code=204, tags=["ops"])
def reset() -> Response:
    """Restore seed state. Test and demo support only."""
    store.reset()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- accounts


@app.get("/accounts", response_model=list[Account], tags=["chart of accounts"])
def list_accounts() -> list[Account]:
    """Chart of accounts — the set of valid GL coding targets."""
    return store.list_accounts()


@app.get("/accounts/{account_id}", response_model=Account, tags=["chart of accounts"])
def get_account(account_id: str) -> Account:
    return store.get_account(account_id)


# ---------------------------------------------------------------------- vendors


@app.get("/vendors", response_model=list[Vendor], tags=["vendors"])
def list_vendors(
    name: Annotated[str | None, Query(description="Case-insensitive substring match.")] = None,
) -> list[Vendor]:
    """Vendor list.

    Substring search only. Fuzzy entity resolution is the agent's job (FR-3.3)
    and lives in our platform, not in the ERP — a real ERP would not offer it
    either, so putting it here would hide work the connector must actually do.
    """
    return store.list_vendors(name_contains=name)


@app.get("/vendors/{vendor_id}", response_model=Vendor, tags=["vendors"])
def get_vendor(vendor_id: str) -> Vendor:
    return store.get_vendor(vendor_id)


# -------------------------------------------------------------- purchase orders


@app.get(
    "/purchase-orders/{doc_number}",
    response_model=PurchaseOrder,
    tags=["three-way match"],
)
def get_purchase_order(doc_number: str) -> PurchaseOrder:
    """What was ordered. First leg of the three-way match."""
    return store.get_purchase_order(doc_number)


@app.get(
    "/purchase-orders/{doc_number}/goods-receipts",
    response_model=list[GoodsReceipt],
    tags=["three-way match"],
)
def list_goods_receipts(doc_number: str) -> list[GoodsReceipt]:
    """What was received. Second leg of the match.

    An empty list is a valid answer meaning nothing has been received yet —
    which is precisely the missing-GRN exception the match must detect, so it is
    not an error.
    """
    return store.list_goods_receipts(po_doc_number=doc_number)


# ------------------------------------------------------------------------ bills


@app.get("/bills", response_model=list[Bill], tags=["bills"])
def list_bills(
    vendor_id: Annotated[str | None, Query()] = None,
    doc_number: Annotated[
        str | None, Query(description="Vendor invoice number (exact).")
    ] = None,
    since: Annotated[
        date | None, Query(description="Only bills on or after this date.")
    ] = None,
) -> list[Bill]:
    """Bill history — the corpus duplicate detection searches (FR-4.3)."""
    return store.list_bills(vendor_id=vendor_id, doc_number=doc_number, since=since)


@app.get("/bills/{bill_id}", response_model=Bill, tags=["bills"])
def get_bill(bill_id: str) -> Bill:
    return store.get_bill(bill_id)


@app.post("/bills", response_model=Bill, status_code=201, tags=["bills"])
def create_bill(
    req: CreateBillRequest,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> Bill:
    """Post a bill — the primary write.

    The ledger applies its own refusals here (blocked vendor, inactive vendor,
    control-account coding, PAID status) regardless of what the caller believes.
    That independence is the point: this is the hard boundary in the
    defence-in-depth model, not a convenience check.
    """
    bill, replayed = store.create_bill(
        req,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint_payload(req.model_dump(mode="json")),
    )
    if replayed:
        # 200 rather than 201: nothing new was created.
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
    return bill


@app.post("/bills/{bill_id}/hold", response_model=Bill, tags=["bills"])
def hold_bill(
    bill_id: str,
    req: HoldBillRequest,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> Bill:
    """Place a bill on hold pending resolution."""
    bill, replayed = store.hold_bill(
        bill_id,
        req,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint_payload(req.model_dump(mode="json")),
    )
    if replayed:
        response.headers["Idempotent-Replay"] = "true"
    return bill


# ----------------------------------------------------------------- payments (r/o)


@app.get("/payments", response_model=list[BillPayment], tags=["payments (read-only)"])
def list_payments(vendor_id: Annotated[str | None, Query()] = None) -> list[BillPayment]:
    """Payment history.

    Read-only by design. There is deliberately no corresponding POST: this
    system does not execute payments (ADR-011).
    """
    return store.list_payments(vendor_id=vendor_id)


# --------------------------------------------------------------------- exceptions


@app.get("/exceptions", response_model=list[ErpException], tags=["exception queue"])
def list_exceptions(
    unresolved_only: Annotated[bool, Query()] = False,
) -> list[ErpException]:
    """The AP exception queue — the work this project exists to attack."""
    return store.list_exceptions(unresolved_only=unresolved_only)


@app.post(
    "/exceptions", response_model=ErpException, status_code=201, tags=["exception queue"]
)
def create_exception(
    req: CreateExceptionRequest,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> ErpException:
    """Raise an exception against an invoice."""
    record, replayed = store.create_exception(
        req,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint_payload(req.model_dump(mode="json")),
        txn_date=TODAY,
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
    return record



