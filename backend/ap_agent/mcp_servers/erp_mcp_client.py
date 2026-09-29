"""`McpErpClient` — the ErpClient the supervisor talks to over MCP.

This is what makes the transport swap invisible: `McpErpClient` satisfies the
exact same `ErpClient` Protocol as Slice 8's `InProcessErpClient`, so the
supervisor and its tools are unchanged whether the ERP lives in-process or
behind an MCP server. Slice 8 built the seam; this is the promised swap.

Two bridges are handled here:

* **sync → async.** The `ErpClient` Protocol is synchronous (the deterministic
  graph nodes call it directly), but MCP is async. Each call is run on a
  dedicated background event loop owned by this client, so a synchronous caller
  gets a synchronous answer without an event loop of its own.
* **wire JSON → canonical models.** The MCP server returns canonical-shaped
  JSON as text content; this client parses it back into the project's
  `PurchaseOrder` / `GoodsReceipt` / `Vendor` / `HistoricalBill` types, so the
  policy engine downstream sees the same domain objects either way.

The purpose-scoped token is injected by the auth interceptor (see
`interceptors.py`), so this client is constructed *with* a token (default
`erp-writer`, since the pipeline both reads and posts) and the individual calls
never handle credentials. A read-only deployment would construct it with an
`erp-reader` token, and the server would then refuse any write — the same
control, chosen at construction.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from ap_agent.core.canonical import (
    BankDetails,
    GoodsReceipt,
    LineItem,
    PurchaseOrder,
    Vendor,
    VendorStatus,
)
from ap_agent.core.primitives import Money
from ap_agent.errors import ErrorContext, PolicyViolationError
from ap_agent.policy.context import HistoricalBill

_REPO_ROOT = Path(__file__).resolve().parents[3]


class McpErpClient:
    """`ErpClient` over an MCP stdio server. Drop-in for `InProcessErpClient`."""

    def __init__(
        self,
        *,
        token: str = "erp-writer",  # noqa: S107 - a purpose-scope id, not a secret
        command: list[str] | None = None,
    ) -> None:
        self._token = token
        self._command = command or [
            "uv",
            "run",
            "python",
            "-m",
            "ap_agent.mcp_servers.erp_server",
        ]
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._tools: dict[str, Any] | None = None

    # ---------------------------------------------------------- async bridge
    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _get_tools(self) -> dict[str, Any]:
        if self._tools is None:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            from ap_agent.mcp_servers.interceptors import default_interceptors

            client = MultiServerMCPClient(
                {
                    "erp": {
                        "command": self._command[0],
                        "args": self._command[1:],
                        "transport": "stdio",
                        "cwd": str(_REPO_ROOT / "backend"),
                    }
                },
                tool_interceptors=default_interceptors(self._token),
                # Raise on a tool error rather than returning it as a text
                # result: a server-side refusal (permission denied, ledger
                # rejection) must surface as an exception this client can turn
                # into a typed PolicyViolationError, not a string a caller might
                # mistake for success.
                handle_tool_errors=False,
            )
            tools = await client.get_tools()
            self._tools = {t.name: t for t in tools}
        return self._tools

    def _call(self, tool: str, args: dict[str, Any]) -> Any:
        async def _invoke() -> Any:
            tools = await self._get_tools()
            if tool not in tools:
                raise KeyError(f"MCP ERP server exposes no tool {tool!r}")
            return await tools[tool].ainvoke(args)

        raw = self._run(_invoke())
        return _parse_content(raw)

    # --------------------------------------------------------------- reads
    def get_purchase_order(self, doc_number: str) -> PurchaseOrder | None:
        data = self._call("get_purchase_order", {"po_number": doc_number})
        if data is None:
            return None
        return _po_from_wire(data)

    def get_goods_receipt(self, po_doc_number: str) -> GoodsReceipt | None:
        data = self._call("get_goods_receipt", {"po_number": po_doc_number})
        if data is None:
            return None
        return _grn_from_wire(data)

    def get_vendor(self, vendor_id: str) -> Vendor | None:
        data = self._call("get_vendor", {"vendor_id": vendor_id})
        if data is None:
            return None
        return _vendor_from_wire(data)

    def list_historical_bills(
        self, *, vendor_id: str | None, since: date
    ) -> tuple[HistoricalBill, ...]:
        if vendor_id is None:
            # The server requires a vendor id; with none, there is no history to
            # compare (duplicate detection falls back to printed-name matching
            # upstream), so return empty rather than a broad unscoped query.
            return ()
        rows = self._call(
            "list_historical_bills", {"vendor_id": vendor_id, "since": since.isoformat()}
        )
        # A list tool returns None (empty), one dict (single bill), or a list.
        if rows is None:
            return ()
        if isinstance(rows, dict):
            rows = [rows]
        return tuple(_bill_from_wire(r) for r in rows)

    # --------------------------------------------------------------- writes
    def post_bill(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        doc_number: str,
        vendor_id: str,
        txn_date: date,
        total_amount: Money,
        gl_account: str | None,
        po_doc_number: str | None,
        private_note: str | None,
    ) -> dict[str, Any]:
        return self._call_write(
            "post_bill",
            {
                "idempotency_key": idempotency_key,
                "doc_number": doc_number,
                "vendor_id": vendor_id,
                "txn_date": txn_date.isoformat(),
                "total_amount": str(total_amount.amount),
                "currency": total_amount.currency,
                "gl_account": gl_account,
                "po_number": po_doc_number,
                "rationale": private_note or "",
            },
            tenant_id=tenant_id,
        )

    def raise_exception(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        kind: str,
        detail: str,
        raised_by: str,
        vendor_id: str | None,
        invoice_number: str | None,
        po_doc_number: str | None,
        amount: Money | None,
    ) -> dict[str, Any]:
        return self._call_write(
            "raise_exception",
            {
                "idempotency_key": idempotency_key,
                "kind": kind,
                "detail": detail,
                "raised_by": raised_by,
                "vendor_id": vendor_id,
                "invoice_number": invoice_number,
                "po_number": po_doc_number,
                "amount": str(amount.amount) if amount is not None else None,
            },
            tenant_id=tenant_id,
        )

    def _call_write(self, tool: str, args: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
        """A write call that turns a server-side refusal into a first-party
        `PolicyViolationError`, matching `InProcessErpClient`'s contract so the
        supervisor behaves identically across transports."""

        async def _invoke() -> Any:
            tools = await self._get_tools()
            return await tools[tool].ainvoke(args)

        try:
            raw = self._run(_invoke())
        except Exception as exc:  # noqa: BLE001 - re-raised as typed
            # A ToolError from the server (permission denial, ledger refusal,
            # validation) arrives here. It is a refusal, not a transport fault.
            raise PolicyViolationError(
                "erp_mcp_refusal",
                str(exc),
                context=ErrorContext(stage="erp.mcp", tenant_id=tenant_id, inputs={"tool": tool}),
            ) from exc
        parsed = _parse_content(raw)
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    def close(self) -> None:
        """Stop the background event loop. Safe to call more than once."""
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)


# ------------------------------------------------------------ wire decoding


def _parse_content(raw: Any) -> Any:
    """Decode an MCP tool result into a Python object.

    langchain-mcp-adapters returns tool content as a list of typed blocks
    (``{"type": "text", "text": "..."}``) or, for structured output, the object
    directly. Handle both: a text block carrying JSON is parsed; a bare
    dict/list/None is returned as-is; ``"null"`` decodes to None.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return json.loads(raw) if raw.strip() else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        # An empty content list is how a tool returning ``None`` (e.g. a missing
        # PO) arrives over the wire — decode it to None, not an empty payload.
        if not raw:
            return None
        # MCP content blocks (``{"type": "text", "text": "..."}``). FastMCP emits
        # ONE block per element of a list-returning tool, each block a complete
        # JSON object — so decode each block separately. A single block decodes
        # to that object (a scalar tool result); multiple blocks decode to the
        # list the tool returned. A non-text-block list is already decoded.
        text_blocks = [b for b in raw if isinstance(b, dict) and b.get("type") == "text"]
        if not text_blocks:
            return raw
        decoded = [
            json.loads(b["text"]) for b in text_blocks if str(b.get("text", "")).strip()
        ]
        if not decoded:
            return None
        return decoded[0] if len(decoded) == 1 else decoded
    # Any other scalar (unlikely from MCP) — hand back untouched.
    return raw


def _money_from_wire(obj: dict[str, Any]) -> Money:
    return Money(amount=Decimal(str(obj["amount"])), currency=obj["currency"])


def _po_from_wire(data: dict[str, Any]) -> PurchaseOrder:
    currency = data["currency"]
    lines = tuple(
        LineItem(
            description=ln["description"],
            quantity=Decimal(str(ln["quantity"])),
            unit_price=Money(amount=Decimal(str(ln["unit_price"])), currency=currency),
            line_total=Money(amount=Decimal(str(ln["amount"])), currency=currency),
            unit_of_measure=ln.get("uom"),
        )
        for ln in data.get("lines", [])
    )
    return PurchaseOrder(
        po_number=data["po_number"],
        vendor_id=data["vendor_id"],
        currency=currency,
        order_date=date.fromisoformat(data["order_date"]),
        total_amount=_money_from_wire(data["total_amount"]),
        lines=lines,
        status=data.get("status"),
        gl_account=data.get("gl_account"),
        cost_center=data.get("cost_center"),
    )


def _grn_from_wire(data: dict[str, Any]) -> GoodsReceipt:
    lines = tuple(
        LineItem(
            description=ln["description"],
            quantity=Decimal(str(ln["quantity_received"])),
            unit_price=Money.zero("USD"),
            line_total=Money.zero("USD"),
            unit_of_measure=ln.get("uom"),
        )
        for ln in data.get("lines", [])
    )
    return GoodsReceipt(
        grn_number=data["grn_number"],
        po_number=data["po_number"],
        received_date=date.fromisoformat(data["received_date"]),
        lines=lines,
        is_partial=data.get("is_partial", False),
    )


def _vendor_from_wire(data: dict[str, Any]) -> Vendor:
    bank = None
    if data.get("bank_account_last4") or data.get("bank_name") or data.get("bank_account_name"):
        bank = BankDetails(
            account_name=data.get("bank_account_name"),
            account_number_last4=data.get("bank_account_last4"),
            routing_code=data.get("bank_routing_code"),
            bank_name=data.get("bank_name"),
        )
    return Vendor(
        vendor_id=data["vendor_id"],
        legal_name=data["legal_name"],
        display_name=data.get("display_name"),
        status=VendorStatus(data["status"]),
        tax_id=data.get("tax_id"),
        default_currency=data.get("default_currency"),
        default_payment_terms=data.get("default_payment_terms"),
        bank_details=bank,
    )


def _bill_from_wire(data: dict[str, Any]) -> HistoricalBill:
    return HistoricalBill(
        invoice_number=data["invoice_number"],
        vendor_id=data["vendor_id"],
        vendor_name=data.get("vendor_name"),
        amount=_money_from_wire(data["amount"]),
        invoice_date=date.fromisoformat(data["invoice_date"]),
        status=data.get("status"),
    )
