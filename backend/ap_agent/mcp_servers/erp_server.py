"""ERP MCP server — the system-of-action boundary as an MCP server (FR-9.2).

Run as a stdio MCP server (`python -m ap_agent.mcp_servers.erp_server`), this is
the *independent* second layer of the three-layer permission model. It wraps the
mock ERP ledger and exposes a narrow, typed toolset, and it enforces its own
server-side permission scopes and input validation **regardless of what the
agent believes**. The agent's RBAC filter (Layer 1) can be removed entirely and
a write with a read-only token is still refused here — that is the whole point
of defence in depth, and a test proves it by bypassing Layer 1.

Two properties are load-bearing:

* **The canonical↔ERP field mapping lives here, not in pipeline logic**
  (FR-3.4). The tools speak the project's canonical shapes (account *numbers*,
  `po_number`, `vendor_id`); the QBO field names (`AcctNum`, `DocNumber`,
  `EntityRef`) and the account-number↔account-id translation are contained in
  this server. Onboarding a different ERP is a new server, not a pipeline edit.

* **No payment tool exists.** Not disabled — absent. The write surface is
  `post_bill` (approved-for-payment) and `raise_exception`. There is no scope
  and no tool that moves money (ADR-011).

Permission model: every tool takes an explicit ``token`` argument (stdio has no
HTTP headers) and calls ``require_scope`` before doing anything. The token is a
purpose-scoped, least-privilege grant — a reader token cannot write — and the
check is a Python guard, not prompt text, so a model cannot argue past it
(FR-9.4).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ap_agent.mcp_servers.permissions import PermissionDenied, Scope, require_scope

mcp = FastMCP("ap-erp")


# --------------------------------------------------------------- store access


def _store() -> Any:
    """The mock ERP ledger store. Imported lazily so the module imports without
    the mock ERP on the path (e.g. under mypy in `ap_agent`)."""
    from app.store import store

    return store


def _guard(token: str | None, scope: Scope, tool: str) -> None:
    """Scope check that converts a server-side refusal into an MCP ToolError.

    A `PermissionDenied` becomes a `ToolError` so it reaches the MCP client as a
    failed tool call — visible and typed, never a silent success.
    """
    try:
        require_scope(token, scope, tool=tool)
    except PermissionDenied as exc:
        raise ToolError(str(exc)) from exc


# ------------------------------------------------------- canonical<->ERP map


def _account_number_to_id(account_number: str | None) -> str | None:
    if account_number is None:
        return None
    for account in _store().list_accounts():
        if account.AcctNum == account_number:
            return str(account.Id)
    return account_number  # let the ledger 404 loudly on a bad number


def _account_id_to_number(account_id: str | None) -> str | None:
    if account_id is None:
        return None
    for account in _store().list_accounts():
        if account.Id == account_id:
            return str(account.AcctNum)
    return None


def _po_to_canonical(po: Any) -> dict[str, Any]:
    currency = po.CurrencyRef.value
    return {
        "po_number": po.DocNumber,
        "vendor_id": po.VendorRef.value,
        "currency": currency,
        "order_date": po.TxnDate.isoformat(),
        "total_amount": {"amount": str(po.TotalAmt), "currency": currency},
        "gl_account": _account_id_to_number(po.AccountRef.value if po.AccountRef else None),
        "cost_center": po.ClassRef.value if po.ClassRef else None,
        "status": po.POStatus.value if hasattr(po.POStatus, "value") else str(po.POStatus),
        "lines": [
            {
                "description": ln.Description,
                "quantity": str(ln.Qty) if ln.Qty is not None else "1",
                "unit_price": str(ln.UnitPrice) if ln.UnitPrice is not None else str(ln.Amount),
                "amount": str(ln.Amount),
                "uom": getattr(ln, "UOM", None),
            }
            for ln in po.Line
        ],
    }


# ----------------------------------------------------------------- read tools


@mcp.tool(
    name="get_purchase_order",
    description=(
        "Read the purchase order for a PO number. Returns the PO in canonical "
        "shape, or null if none exists. Requires an erp:read-scoped token."
    ),
)
def get_purchase_order(token: str, po_number: str) -> dict[str, Any] | None:
    _guard(token, Scope.ERP_READ, "get_purchase_order")
    from app.store import ErpNotFoundError

    try:
        po = _store().get_purchase_order(po_number)
    except ErpNotFoundError:
        return None
    return _po_to_canonical(po)


@mcp.tool(
    name="get_goods_receipt",
    description=(
        "Read the goods receipt for a PO number. Returns it in canonical shape, "
        "or null if nothing has been received. Requires erp:read."
    ),
)
def get_goods_receipt(token: str, po_number: str) -> dict[str, Any] | None:
    _guard(token, Scope.ERP_READ, "get_goods_receipt")
    from app.store import ErpNotFoundError

    try:
        receipts = _store().list_goods_receipts(po_doc_number=po_number)
    except ErpNotFoundError:
        return None
    if not receipts:
        return None
    grn = receipts[0]
    return {
        "grn_number": grn.DocNumber,
        "po_number": po_number,
        "received_date": grn.TxnDate.isoformat(),
        "is_partial": grn.IsPartial,
        "lines": [
            {"description": ln.Description, "quantity_received": str(ln.QtyReceived), "uom": getattr(ln, "UOM", None)}
            for ln in grn.Line
        ],
    }


@mcp.tool(
    name="get_vendor",
    description="Read a vendor record by id in canonical shape, or null. Requires erp:read.",
)
def get_vendor(token: str, vendor_id: str) -> dict[str, Any] | None:
    _guard(token, Scope.ERP_READ, "get_vendor")
    from app.store import ErpNotFoundError

    try:
        v = _store().get_vendor(vendor_id)
    except ErpNotFoundError:
        return None
    if v.Blocked:
        status = "blocked"
    elif not v.Active:
        status = "inactive"
    else:
        status = "active"
    return {
        "vendor_id": v.Id,
        "legal_name": v.CompanyName or v.DisplayName,
        "display_name": v.DisplayName,
        "status": status,
        "tax_id": v.PrimaryTaxIdentifier,
        "default_currency": v.CurrencyRef.value if v.CurrencyRef else None,
        "default_payment_terms": v.TermRef.value if v.TermRef else None,
        "bank_account_last4": v.BankAccountLast4,
        "bank_name": v.BankName,
        "bank_account_name": v.BankAccountName,
        "bank_routing_code": v.BankRoutingCode,
    }


@mcp.tool(
    name="list_historical_bills",
    description=(
        "List prior bills for a vendor on or after a date (ISO), for duplicate "
        "detection. Requires erp:read."
    ),
)
def list_historical_bills(token: str, vendor_id: str, since: str) -> list[dict[str, Any]]:
    _guard(token, Scope.ERP_READ, "list_historical_bills")
    try:
        since_date = date.fromisoformat(since)
    except ValueError as exc:
        raise ToolError(f"'since' must be an ISO date, got {since!r}.") from exc
    bills = _store().list_bills(vendor_id=vendor_id, since=since_date)
    return [
        {
            "invoice_number": b.DocNumber,
            "vendor_id": b.VendorRef.value,
            "vendor_name": b.VendorRef.name,
            "amount": {"amount": str(b.TotalAmt), "currency": b.CurrencyRef.value},
            "invoice_date": b.TxnDate.isoformat(),
            "status": b.BillStatus.value if hasattr(b.BillStatus, "value") else str(b.BillStatus),
        }
        for b in bills
    ]


# ---------------------------------------------------------------- write tools


@mcp.tool(
    name="post_bill",
    description=(
        "Post an approved bill to the ERP. Idempotent per idempotency_key. "
        "Requires an erp:write-scoped token. Does NOT execute payment — money "
        "never moves through this system."
    ),
)
def post_bill(
    token: str,
    idempotency_key: str,
    doc_number: str,
    vendor_id: str,
    txn_date: str,
    total_amount: str,
    currency: str,
    rationale: str,
    gl_account: str | None = None,
    po_number: str | None = None,
) -> dict[str, Any]:
    _guard(token, Scope.ERP_WRITE, "post_bill")
    from app.models import CreateBillRequest
    from app.store import (
        ErpConflictError,
        ErpNotFoundError,
        ErpValidationError,
        fingerprint_payload,
    )

    try:
        amount = Decimal(total_amount)
        txn = date.fromisoformat(txn_date)
    except (InvalidOperation, ValueError) as exc:
        raise ToolError(f"Invalid amount or date: {total_amount!r}, {txn_date!r}.") from exc

    req = CreateBillRequest(
        DocNumber=doc_number,
        VendorId=vendor_id,
        TxnDate=txn,
        TotalAmt=amount,
        CurrencyCode=currency,
        AccountId=_account_number_to_id(gl_account),
        PurchaseOrderDocNumber=po_number,
        PrivateNote=rationale,
    )
    try:
        bill, replayed = _store().create_bill(
            req,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint_payload(req.model_dump(mode="json")),
        )
    except (ErpValidationError, ErpConflictError, ErpNotFoundError) as exc:
        # The ledger's own refusal (blocked vendor, control account, PAID, key
        # reuse). Surface it as a ToolError — the third independent layer.
        raise ToolError(str(exc)) from exc
    return {"bill": bill.model_dump(mode="json"), "replayed": replayed}


@mcp.tool(
    name="raise_exception",
    description=(
        "Raise an AP exception into the ledger's exception queue. Requires "
        "erp:write."
    ),
)
def raise_exception(
    token: str,
    idempotency_key: str,
    kind: str,
    detail: str,
    raised_by: str,
    vendor_id: str | None = None,
    invoice_number: str | None = None,
    po_number: str | None = None,
    amount: str | None = None,
) -> dict[str, Any]:
    _guard(token, Scope.ERP_WRITE, "raise_exception")
    from app.models import CreateExceptionRequest, ExceptionKind
    from app.seed import TODAY
    from app.store import ErpValidationError, fingerprint_payload

    try:
        kind_enum = ExceptionKind(kind)
    except ValueError:
        kind_enum = ExceptionKind.OTHER
    amount_dec: Decimal | None = None
    if amount is not None:
        try:
            amount_dec = Decimal(amount)
        except InvalidOperation as exc:
            raise ToolError(f"Invalid amount {amount!r}.") from exc

    req = CreateExceptionRequest(
        Kind=kind_enum,
        Detail=detail,
        RaisedBy=raised_by,
        VendorId=vendor_id,
        InvoiceNumber=invoice_number,
        PurchaseOrderDocNumber=po_number,
        Amount=amount_dec,
    )
    try:
        record, replayed = _store().create_exception(
            req,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint_payload(req.model_dump(mode="json")),
            txn_date=TODAY,
        )
    except ErpValidationError as exc:
        raise ToolError(str(exc)) from exc
    return {"exception": record.model_dump(mode="json"), "replayed": replayed}


def main() -> None:
    """Entry point: run the server over stdio (`python -m ...erp_server`)."""
    import sys
    from pathlib import Path

    # Ensure the mock ERP (`app.*`) is importable when launched as a subprocess.
    repo_root = Path(__file__).resolve().parents[3]
    mock_erp = repo_root / "mock_erp"
    if str(mock_erp) not in sys.path:
        sys.path.insert(0, str(mock_erp))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
