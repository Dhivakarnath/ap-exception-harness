"""ERP client seam — the boundary the graph's tools call to reach the ledger.

The graph never touches the mock ERP's QuickBooks-shaped objects directly. It
goes through this `ErpClient` Protocol, which speaks in the project's canonical
domain types (`PurchaseOrder`, `GoodsReceipt`, `HistoricalBill`, `Vendor`) and
hides the QBO field names (`DocNumber`, `VendorRef`, `TotalAmt`) behind the
mapping in this module. That mapping is the connector's job (FR-3.4); putting it
here — at the seam — rather than sprinkled through pipeline logic is what keeps
"onboard a customer" a connector change, not a fork.

**Why a Protocol with an in-process implementation now.** Slice 8 wires the
graph end-to-end against the mock ERP in-process (the demo checkpoint). Slice 9
replaces the transport with real MCP tool servers. Both satisfy the same
`ErpClient` Protocol, so the graph and its tools do not change when the
transport does — the seam is the point. `InProcessErpClient` talks to the mock
ERP's `store` singleton directly (the same object the mock's FastAPI routes
use), which is exactly what the mock's own tests do, so no HTTP server needs to
be running for a graph test.

**Writes are idempotent and money never moves.** `post_bill` requires an
idempotency key and returns the ledger's own result (including a replay), and
there is deliberately no `pay`/`disburse` method — the mock ERP has no such
endpoint and this client must not invent one (ADR-011). A blocked/inactive
vendor or a control-account coding is refused by the ledger itself, independent
of anything the agent believes; this client surfaces that refusal as a
first-party error rather than swallowing it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from ap_agent.core.canonical import (
    GoodsReceipt,
    LineItem,
    PurchaseOrder,
    Vendor,
    VendorStatus,
)
from ap_agent.core.primitives import Money
from ap_agent.errors import ErrorContext, PolicyViolationError
from ap_agent.policy.context import HistoricalBill


class ErpClient(Protocol):
    """The narrow ERP surface the graph's tools depend on.

    Reads for the three-way match and duplicate detection; two writes (post a
    bill, raise an exception). No payment execution — that capability is absent
    by design, not disabled.
    """

    def get_purchase_order(self, doc_number: str) -> PurchaseOrder | None:
        """The PO for a document number, or None if it does not exist.

        None is a legitimate answer (a genuinely non-PO invoice, or a bad PO
        reference the match must flag), not an error.
        """
        ...

    def get_goods_receipt(self, po_doc_number: str) -> GoodsReceipt | None:
        """The goods receipt for a PO, or None when nothing has been received —
        which is exactly the missing-GRN exception the match must detect."""
        ...

    def get_vendor(self, vendor_id: str) -> Vendor | None:
        ...

    def list_historical_bills(
        self, *, vendor_id: str | None, since: date
    ) -> tuple[HistoricalBill, ...]:
        """Prior bills for duplicate detection, scoped by date (FR-4.3)."""
        ...

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
    ) -> dict[str, object]:
        """Post an approved bill. Idempotent per key. Returns the ledger result.

        Raises `PolicyViolationError` when the ledger refuses (blocked vendor,
        control-account coding, PAID status) — the second, independent layer of
        defence at the system-of-action boundary."""
        ...

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
    ) -> dict[str, object]:
        """Raise an AP exception into the ledger's exception queue."""
        ...


class TracedErpClient:
    """An `ErpClient` proxy that makes every ERP call an OTEL tool span.

    A reviewer opening a run's trace should see the ERP reads and writes as their
    own spans — ``ap.tool.erp.get_purchase_order``, ``ap.tool.erp.post_bill`` —
    nested under the node that made them, so "the policy node took 40ms" resolves
    into "three ERP reads of ~12ms each". The span carries the call's arguments,
    PII-redacted (a vendor's bank details or a private note must never reach a
    span), and this proxy speaks the same `ErpClient` Protocol as the thing it
    wraps, so it works identically for the in-process and MCP clients. When
    tracing is off, `tool_span` yields a no-op, so this adds nothing.
    """

    def __init__(self, inner: ErpClient) -> None:
        self._inner = inner
        self._calls: list[dict[str, object]] = []

    def _span(self, method: str, args: dict[str, Any]) -> Any:
        from ap_agent.observability.redaction import redact_pii_deep
        from ap_agent.observability.tracing import tool_span

        cm = tool_span(f"erp.{method}")
        # Attach redacted args when the span is real; tool_span returns a context
        # manager whose entered value is the span (or None when tracing is off).
        return _SpanWithArgs(cm, redact_pii_deep(args))

    @property
    def calls(self) -> list[dict[str, object]]:
        """A copy of the real, redacted ERP call trajectory."""
        return [dict(call) for call in self._calls]

    def reset_calls(self) -> None:
        self._calls.clear()

    def drain_calls(self) -> list[dict[str, object]]:
        calls = self.calls
        self.reset_calls()
        return calls

    def _record(self, method: str, args: dict[str, Any]) -> None:
        from ap_agent.observability.redaction import redact_pii_deep

        self._calls.append(
            {
                "name": f"erp.{method}",
                "input_parameters": redact_pii_deep(args),
            }
        )

    def get_purchase_order(self, doc_number: str) -> PurchaseOrder | None:
        args = {"doc_number": doc_number}
        self._record("get_purchase_order", args)
        with self._span("get_purchase_order", args):
            return self._inner.get_purchase_order(doc_number)

    def get_goods_receipt(self, po_doc_number: str) -> GoodsReceipt | None:
        args = {"po_doc_number": po_doc_number}
        self._record("get_goods_receipt", args)
        with self._span("get_goods_receipt", args):
            return self._inner.get_goods_receipt(po_doc_number)

    def get_vendor(self, vendor_id: str) -> Vendor | None:
        args = {"vendor_id": vendor_id}
        self._record("get_vendor", args)
        with self._span("get_vendor", args):
            return self._inner.get_vendor(vendor_id)

    def list_historical_bills(
        self, *, vendor_id: str | None, since: date
    ) -> tuple[HistoricalBill, ...]:
        args = {"vendor_id": vendor_id, "since": str(since)}
        self._record("list_historical_bills", args)
        with self._span("list_historical_bills", args):
            return self._inner.list_historical_bills(vendor_id=vendor_id, since=since)

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
    ) -> dict[str, object]:
        args = {
            "doc_number": doc_number,
            "vendor_id": vendor_id,
            "total_amount": str(total_amount.amount),
            "gl_account": gl_account,
            "po_doc_number": po_doc_number,
            # private_note is intentionally omitted: free text may carry PII.
        }
        self._record("post_bill", args)
        with self._span("post_bill", args):
            return self._inner.post_bill(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                doc_number=doc_number,
                vendor_id=vendor_id,
                txn_date=txn_date,
                total_amount=total_amount,
                gl_account=gl_account,
                po_doc_number=po_doc_number,
                private_note=private_note,
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
    ) -> dict[str, object]:
        args = {
            "kind": kind,
            "raised_by": raised_by,
            "vendor_id": vendor_id,
            "invoice_number": invoice_number,
            "po_doc_number": po_doc_number,
        }
        self._record("raise_exception", args)
        with self._span("raise_exception", args):
            return self._inner.raise_exception(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                kind=kind,
                detail=detail,
                raised_by=raised_by,
                vendor_id=vendor_id,
                invoice_number=invoice_number,
                po_doc_number=po_doc_number,
                amount=amount,
            )


class _SpanWithArgs:
    """A context manager that opens ``inner`` and, if a real span is produced,
    stamps the (already-redacted) call args onto it as ``ap.arg.*`` attributes.

    Keeping this tiny wrapper avoids duplicating the "set attributes if the span
    exists" dance in every ``TracedErpClient`` method."""

    def __init__(self, inner_cm: Any, redacted_args: dict[str, Any]) -> None:
        self._inner_cm = inner_cm
        self._args = redacted_args

    def __enter__(self) -> Any:
        import contextlib

        span = self._inner_cm.__enter__()
        if span is not None:
            for key, value in self._args.items():
                if value is not None:
                    # Instrumentation must never fail a run; a bad attribute is
                    # dropped, not raised.
                    with contextlib.suppress(Exception):
                        span.set_attribute(f"ap.arg.{key}", str(value))
        return span

    def __exit__(self, *exc: Any) -> Any:
        return self._inner_cm.__exit__(*exc)


# --------------------------------------------------------------- QBO -> canonical


def _entity_id(ref: object) -> str:
    """Pull the id out of a QBO `EntityRef` ({"value","name"})."""
    value = getattr(ref, "value", None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"EntityRef has no usable value: {ref!r}")
    return value


def _map_vendor_status(*, active: bool, blocked: bool) -> VendorStatus:
    # Order matters: blocked dominates inactive, because "blocked" carries the
    # reason a bill must never post, and collapsing it into "inactive" would lose
    # that (the mock models them as separate flags for exactly this reason).
    if blocked:
        return VendorStatus.BLOCKED
    if not active:
        return VendorStatus.INACTIVE
    return VendorStatus.ACTIVE



def _txn_line_to_line_item(line: Any, currency: str) -> LineItem:
    """Map a QBO `TxnLine` to a canonical `LineItem`.

    QBO carries `Amount` always and `Qty`/`UnitPrice` only on item-based lines.
    When quantity is absent (a service line), quantity defaults to 1 and the
    unit price equals the amount — a faithful mapping, since a one-unit service
    at price = amount reconciles arithmetically.
    """
    amount = Money(amount=Decimal(str(line.Amount)), currency=currency)
    qty_raw = getattr(line, "Qty", None)
    unit_raw = getattr(line, "UnitPrice", None)
    quantity = Decimal(str(qty_raw)) if qty_raw is not None else Decimal("1")
    unit_price = (
        Money(amount=Decimal(str(unit_raw)), currency=currency)
        if unit_raw is not None
        else amount
    )
    return LineItem(
        description=line.Description,
        quantity=quantity,
        unit_price=unit_price,
        line_total=amount,
        unit_of_measure=getattr(line, "UOM", None),
    )


class InProcessErpClient:
    """`ErpClient` backed by the mock ERP `store` singleton, in-process.

    Talks to the same `LedgerStore` object the mock ERP's FastAPI routes use, so
    a graph test exercises the real ledger (including its idempotency and its
    business refusals) with no HTTP server running. Slice 9 replaces this with an
    MCP-transport client satisfying the same Protocol.
    """

    def __init__(self, store: Any = None) -> None:
        if store is None:
            from app.store import store as default_store

            store = default_store
        self._store: Any = store

    # ------------------------------------------------------------------ reads
    def get_purchase_order(self, doc_number: str) -> PurchaseOrder | None:
        from app.store import ErpNotFoundError

        try:
            po = self._store.get_purchase_order(doc_number)
        except ErpNotFoundError:
            return None
        currency = po.CurrencyRef.value
        return PurchaseOrder(
            po_number=po.DocNumber,
            vendor_id=_entity_id(po.VendorRef),
            currency=currency,
            order_date=po.TxnDate,
            total_amount=Money(amount=Decimal(str(po.TotalAmt)), currency=currency),
            lines=tuple(_txn_line_to_line_item(line, currency) for line in po.Line),
            status=po.POStatus.value if hasattr(po.POStatus, "value") else str(po.POStatus),
            # The canonical model codes to account *numbers* (e.g. '6500'); the
            # ERP references accounts by internal id. Resolve inbound so an
            # inherited PO code is a real GL account number, symmetric with the
            # outbound `_account_id_for_gl` mapping.
            gl_account=(
                self._gl_number_for_account_id(_entity_id(po.AccountRef))
                if po.AccountRef is not None
                else None
            ),
            cost_center=(po.ClassRef.value if po.ClassRef is not None else None),
            department=(po.DepartmentRef.value if po.DepartmentRef is not None else None),
            project=(po.ProjectRef.value if po.ProjectRef is not None else None),
            approved_by=po.ApprovedBy,
        )

    def get_goods_receipt(self, po_doc_number: str) -> GoodsReceipt | None:
        from app.store import ErpNotFoundError

        try:
            receipts = self._store.list_goods_receipts(po_doc_number=po_doc_number)
        except ErpNotFoundError:
            return None
        if not receipts:
            # No receipt is a legitimate answer (nothing received yet) — the
            # missing-GRN case the three-way match must detect, not an error.
            return None
        grn = receipts[0]
        lines = tuple(
            LineItem(
                description=line.Description,
                quantity=Decimal(str(line.QtyReceived)),
                # A GRN records quantities, not prices; unit price is unknown on
                # a receipt, so a zero-priced line carries the received quantity
                # for the match without asserting a price it does not have.
                unit_price=Money.zero("USD"),
                line_total=Money.zero("USD"),
                unit_of_measure=getattr(line, "UOM", None),
            )
            for line in grn.Line
        )
        return GoodsReceipt(
            grn_number=grn.DocNumber,
            po_number=po_doc_number,
            received_date=grn.TxnDate,
            lines=lines,
            received_by=grn.ReceivedBy,
            is_partial=grn.IsPartial,
        )

    def get_vendor(self, vendor_id: str) -> Vendor | None:
        from app.store import ErpNotFoundError

        try:
            v = self._store.get_vendor(vendor_id)
        except ErpNotFoundError:
            return None
        from ap_agent.core.canonical import BankDetails

        bank = None
        if v.BankAccountLast4 or v.BankName or v.BankAccountName:
            bank = BankDetails(
                account_name=v.BankAccountName,
                account_number_last4=v.BankAccountLast4,
                routing_code=v.BankRoutingCode,
                bank_name=v.BankName,
            )
        return Vendor(
            vendor_id=v.Id,
            legal_name=v.CompanyName or v.DisplayName,
            display_name=v.DisplayName,
            status=_map_vendor_status(active=v.Active, blocked=v.Blocked),
            tax_id=v.PrimaryTaxIdentifier,
            default_currency=(v.CurrencyRef.value if v.CurrencyRef is not None else None),
            default_payment_terms=(v.TermRef.value if v.TermRef is not None else None),
            bank_details=bank,
        )

    def list_historical_bills(
        self, *, vendor_id: str | None, since: date
    ) -> tuple[HistoricalBill, ...]:
        bills = self._store.list_bills(vendor_id=vendor_id, since=since)
        out: list[HistoricalBill] = []
        for bill in bills:
            out.append(
                HistoricalBill(
                    invoice_number=bill.DocNumber,
                    vendor_id=_entity_id(bill.VendorRef),
                    vendor_name=bill.VendorRef.name,
                    amount=Money(
                        amount=Decimal(str(bill.TotalAmt)), currency=bill.CurrencyRef.value
                    ),
                    invoice_date=bill.TxnDate,
                    status=(
                        bill.BillStatus.value
                        if hasattr(bill.BillStatus, "value")
                        else str(bill.BillStatus)
                    ),
                )
            )
        return tuple(out)

    # ----------------------------------------------------------------- writes
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
    ) -> dict[str, object]:
        from app.models import CreateBillRequest
        from app.store import (
            ErpConflictError,
            ErpNotFoundError,
            ErpValidationError,
            fingerprint_payload,
        )

        account_id = self._account_id_for_gl(gl_account)
        req = CreateBillRequest(
            DocNumber=doc_number,
            VendorId=vendor_id,
            TxnDate=txn_date,
            TotalAmt=total_amount.amount,
            CurrencyCode=total_amount.currency,
            AccountId=account_id,
            PurchaseOrderDocNumber=po_doc_number,
            PrivateNote=private_note,
        )
        ctx = ErrorContext(
            stage="erp.post_bill",
            tenant_id=tenant_id,
            inputs={"doc_number": doc_number, "vendor_id": vendor_id},
        )
        try:
            bill, replayed = self._store.create_bill(
                req,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint_payload(req.model_dump(mode="json")),
            )
        except ErpValidationError as exc:
            # The ledger refused on business grounds (blocked vendor, control
            # account, PAID). This is the independent second layer working, not a
            # bug — surface it as a policy violation, do not swallow it.
            raise PolicyViolationError("erp_ledger_refusal", str(exc), context=ctx) from exc
        except ErpConflictError as exc:
            raise PolicyViolationError("erp_idempotency_conflict", str(exc), context=ctx) from exc
        except ErpNotFoundError as exc:
            raise PolicyViolationError("erp_reference_not_found", str(exc), context=ctx) from exc
        return {"bill": bill.model_dump(mode="json"), "replayed": replayed}

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
    ) -> dict[str, object]:
        from app.models import CreateExceptionRequest, ExceptionKind
        from app.store import ErpValidationError, fingerprint_payload

        try:
            kind_enum = ExceptionKind(kind)
        except ValueError:
            kind_enum = ExceptionKind.OTHER
        req = CreateExceptionRequest(
            Kind=kind_enum,
            Detail=detail,
            RaisedBy=raised_by,
            VendorId=vendor_id,
            InvoiceNumber=invoice_number,
            PurchaseOrderDocNumber=po_doc_number,
            Amount=amount.amount if amount is not None else None,
        )
        ctx = ErrorContext(stage="erp.raise_exception", tenant_id=tenant_id)
        try:
            record, replayed = self._store.create_exception(
                req,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint_payload(req.model_dump(mode="json")),
                txn_date=req_txn_date(self._store),
            )
        except ErpValidationError as exc:
            raise PolicyViolationError("erp_ledger_refusal", str(exc), context=ctx) from exc
        return {"exception": record.model_dump(mode="json"), "replayed": replayed}

    # --------------------------------------------------------------- helpers
    def _gl_number_for_account_id(self, account_id: str) -> str | None:
        """Resolve an ERP account *id* back to its GL account *number*.

        The inbound half of the account mapping: a PO references its coding
        account by ERP id, but the pipeline reasons in account numbers.
        """
        for account in self._store.list_accounts():
            if account.Id == account_id:
                return str(account.AcctNum)
        return None

    def _account_id_for_gl(self, gl_account: str | None) -> str | None:
        """Resolve a GL account *number* (e.g. '6500') to the ERP account *id*.

        The canonical model codes to account numbers; the ERP keys bills on its
        internal account id. This lookup is part of the outbound mapping: the
        pipeline speaks account numbers, the connector speaks ERP ids.
        """
        if gl_account is None:
            return None
        for account in self._store.list_accounts():
            if account.AcctNum == gl_account:
                return str(account.Id)
        # An account number with no ERP match is a mapping gap, not something to
        # paper over by posting uncoded — let the ledger's own validation speak
        # by passing the number through as the id (it will 404 loudly).
        return gl_account


def req_txn_date(store: Any) -> date:
    """The ledger's notion of 'today' for an exception record.

    Read from the mock ERP seed so exception dates line up with seeded history
    rather than wall-clock time, keeping demos and tests deterministic.
    """
    from app.seed import TODAY

    result: date = TODAY
    return result



def get_erp_client() -> ErpClient:
    """Return the ERP client the configured transport selects (FR-9.2).

    ``in_process`` (default) is the fast Slice-8 seam; ``mcp`` runs the ERP MCP
    server over stdio with independent server-side permission enforcement
    (Slice 9). Both satisfy this module's `ErpClient` Protocol, so the caller —
    the supervisor — is identical regardless of which is chosen. The MCP client
    is imported lazily so selecting the in-process transport pulls in no MCP
    machinery.
    """
    from ap_agent.config import get_settings

    if get_settings().erp_transport == "mcp":
        from ap_agent.mcp_servers.erp_mcp_client import McpErpClient

        return McpErpClient()
    return InProcessErpClient()
