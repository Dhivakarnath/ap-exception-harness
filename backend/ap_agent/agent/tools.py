"""The narrow, typed toolset the supervisor exposes (FR-7.3).

Eight tools, no more. A small, sharply-typed toolset is a security property, not
an aesthetic one: every tool is an action the agent can take against the outside
world, so the surface area of what can go wrong is exactly the length of this
list. The RBAC middleware filters this set per sub-agent, so the extraction
sub-agent can never see `post_erp_action`, and a static test asserts the count
never creeps past eight.

**Two layers, one seam.** Each tool is a thin LangChain `@tool` wrapper over a
plain typed function (`_parse_document`, `_get_po`, ...). The deterministic
supervisor graph calls the plain functions directly — no model round-trip where
the step is deterministic — while a `create_agent` sub-agent calls the same
logic through the `@tool` wrapper when genuine model reasoning is in the loop.
Writing the logic once and wrapping it keeps the two paths from drifting.

**Dependencies are injected, never global.** Tools reach the ERP, the retriever,
and the database through a `ToolContext` handed in at graph-build time, so a test
supplies a scripted ERP client and an in-memory retriever with no monkeypatching.
The tools themselves hold no state.

**The write tools are where the guardrails bite.** `post_erp_action` and
`notify_approver` are the only tools that touch the outside world's state; the
policy-engine `wrap_tool_call` middleware runs the deterministic checks before
either is allowed to proceed, and the ERP itself refuses independently
(defence in depth). There is deliberately no payment tool — the toolset
terminates at "approved for payment" (ADR-011).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from ap_agent.core.primitives import Money
from ap_agent.errors import ErrorContext, HumanInputRequiredError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ap_agent.agent.erp_client import ErpClient
    from ap_agent.agent.notify import Notifier
    from ap_agent.extract.extractor import InvoiceExtractor
    from ap_agent.rag.retriever import HybridRetriever

# The hard cap. The RBAC filter narrows *per sub-agent*; this is the ceiling for
# the whole system. A test asserts len(build_toolset(...)) never exceeds this.
MAX_TOOLS: Final[int] = 8


@dataclass(slots=True)
class ToolContext:
    """Injected dependencies every tool needs. Never global.

    Assembled once when the graph is built and closed over by the tool wrappers.
    Holding these here (rather than importing singletons inside each tool) is
    what lets a test run the whole toolset against scripted doubles.
    """

    tenant_id: str
    erp: ErpClient
    retriever: HybridRetriever
    extractor: InvoiceExtractor
    session_factory: object
    """A zero-arg callable returning a context-managed SQLAlchemy `Session`
    (i.e. `session_scope`), used by the retrieval tools. Typed loosely to avoid
    importing the persistence layer into every tool signature."""
    notifier: Notifier | None = None
    """Escalation sink. None uses the console notifier. Slice 9 supplies a real
    Slack/email adapter behind the same call shape."""


# --------------------------------------------------------------- tool schemas


class ParseDocumentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_path: str = Field(description="Local path to the document to parse.")
    media_type: str = Field(description="MIME type, e.g. 'application/pdf' or 'image/png'.")
    document_id: str = Field(description="Stable id for this document.")


class ExtractFieldsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    invoice_id: str


class RetrievePolicyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="What to ground: the invoice's vendor and line summary.")
    as_of: date = Field(description="The invoice date. Retrieval is scoped to policy in force then.")


class LookupPrecedentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    as_of: date


class GetPurchaseOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    po_number: str


class GetGoodsReceiptArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    po_number: str


class PostErpActionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(description="Stable key; a retry with this key must not double-post.")
    doc_number: str = Field(description="The vendor invoice number.")
    vendor_id: str
    txn_date: date
    total_amount: str = Field(description="Decimal string, e.g. '2400.00'.")
    currency: str = Field(min_length=3, max_length=3)
    gl_account: str | None = Field(default=None, description="GL account number, e.g. '6500'.")
    po_number: str | None = None
    rationale: str = Field(description="Why this is being posted — survives in the ERP note.")


class NotifyApproverArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_tier: str = Field(description="DOA tier that must approve, e.g. 'controller'.")
    reason: str
    invoice_id: str
    amount: str = Field(description="Decimal string of the invoice total.")
    currency: str = Field(min_length=3, max_length=3)


# ------------------------------------------------------------- tool logic (plain)
# The deterministic graph calls these directly. The @tool wrappers below expose
# the same logic to a create_agent sub-agent.


def _parse_document(ctx: ToolContext, args: ParseDocumentArgs) -> dict[str, object]:
    from pathlib import Path

    from ap_agent.ingest.parsing import parse_path

    parsed = parse_path(
        Path(args.document_path),
        media_type=args.media_type,
        document_id=args.document_id,
    )
    return {
        "document_id": args.document_id,
        "strategy": parsed.strategy.value,
        "escalated_to_ocr": parsed.escalated_to_ocr,
        "text_length": parsed.text_length,
        "page_count": parsed.page_count,
        # The parsed object itself is threaded via graph state, not the tool
        # return — the return is the inspectable summary for the model/stream.
    }


def _retrieve(ctx: ToolContext, *, query: str, as_of: date, doc_types: frozenset[str] | None) -> dict[str, object]:
    from ap_agent.rag.rail import apply_rail

    session_scope = ctx.session_factory
    with session_scope() as session:  # type: ignore[operator]
        session_: Session = session
        hits = ctx.retriever.retrieve(
            session_, tenant_id=ctx.tenant_id, query=query, as_of=as_of
        )
    railed = apply_rail(hits, allowed_doc_types=doc_types)
    return {
        "kept": [
            {"citation_ref": c.citation_ref, "doc_type": c.doc_type, "content": c.content}
            for c in railed.kept
        ],
        "dropped": railed.dropped,
        "has_lexical_anchor": railed.has_lexical_anchor,
    }


def _get_po(ctx: ToolContext, po_number: str) -> dict[str, object] | None:
    po = ctx.erp.get_purchase_order(po_number)
    if po is None:
        return None
    return po.model_dump(mode="json")


def _get_grn(ctx: ToolContext, po_number: str) -> dict[str, object] | None:
    grn = ctx.erp.get_goods_receipt(po_number)
    if grn is None:
        return None
    return grn.model_dump(mode="json")


def _post_erp_action(ctx: ToolContext, args: PostErpActionArgs) -> dict[str, object]:
    from decimal import Decimal, InvalidOperation

    try:
        amount = Money(amount=Decimal(args.total_amount), currency=args.currency)
    except (InvalidOperation, ValueError) as exc:
        # A malformed amount is human-fixable, not something to coerce: posting a
        # guessed total is exactly the silent-wrong-payment failure we forbid.
        raise HumanInputRequiredError(
            f"post_erp_action received an unparseable amount {args.total_amount!r}.",
            context=ErrorContext(stage="tool.post_erp_action", tenant_id=ctx.tenant_id),
            cause=exc,
        ) from exc
    return ctx.erp.post_bill(
        tenant_id=ctx.tenant_id,
        idempotency_key=args.idempotency_key,
        doc_number=args.doc_number,
        vendor_id=args.vendor_id,
        txn_date=args.txn_date,
        total_amount=amount,
        gl_account=args.gl_account,
        po_doc_number=args.po_number,
        private_note=args.rationale,
    )


def _notify_approver(ctx: ToolContext, args: NotifyApproverArgs) -> dict[str, object]:
    from ap_agent.agent.notify import get_notifier

    notifier = ctx.notifier if ctx.notifier is not None else get_notifier()
    return notifier.notify(
        tenant_id=ctx.tenant_id,
        required_tier=args.required_tier,
        reason=args.reason,
        invoice_id=args.invoice_id,
        amount=args.amount,
        currency=args.currency,
    )


# ------------------------------------------------------------- @tool wrappers


def build_toolset(ctx: ToolContext) -> list[BaseTool]:
    """Assemble the eight tools, closing over the injected context.

    Returned as a fresh list per graph build so a test can construct an isolated
    toolset over scripted doubles. The RBAC middleware narrows this per sub-agent
    by tool *name*, so the names here are the stable contract.
    """

    @tool("parse_document", args_schema=ParseDocumentArgs)
    def parse_document(document_path: str, media_type: str, document_id: str) -> dict[str, object]:
        """Parse a document into structured markdown, tables, and layout regions.

        Read-only. The first step for any invoice: turns a PDF or image into text
        the extractor can read. Escalates to OCR automatically when the structural
        pass finds too little text."""
        return _parse_document(
            ctx, ParseDocumentArgs(document_path=document_path, media_type=media_type, document_id=document_id)
        )

    @tool("retrieve_policy", args_schema=RetrievePolicyArgs)
    def retrieve_policy(query: str, as_of: date) -> dict[str, object]:
        """Retrieve accounting-policy clauses in force on the invoice date.

        Read-only. Returns policy chunks (not precedent) with their citation
        refs, scoped to this tenant and the as-of date. Use to ground a GL code
        in a policy clause."""
        return _retrieve(ctx, query=query, as_of=as_of, doc_types=frozenset({"accounting_policy"}))

    @tool("lookup_precedent", args_schema=LookupPrecedentArgs)
    def lookup_precedent(query: str, as_of: date) -> dict[str, object]:
        """Look up prior coded-invoice precedent and vendor contracts.

        Read-only. Returns precedent and vendor-contract chunks with citation
        refs, scoped to this tenant and the as-of date. Use when a vendor's
        billing language matches a previously-coded example."""
        return _retrieve(
            ctx, query=query, as_of=as_of, doc_types=frozenset({"coding_precedent", "vendor_contract"})
        )

    @tool("get_purchase_order", args_schema=GetPurchaseOrderArgs)
    def get_purchase_order(po_number: str) -> dict[str, object] | None:
        """Fetch the purchase order for a PO number, or null if none exists.

        Read-only. First leg of the three-way match: what was ordered and
        authorised. A null result means the referenced PO does not exist."""
        return _get_po(ctx, po_number)

    @tool("get_goods_receipt", args_schema=GetGoodsReceiptArgs)
    def get_goods_receipt(po_number: str) -> dict[str, object] | None:
        """Fetch the goods receipt for a PO, or null if nothing was received.

        Read-only. Third leg of the match: what was actually received. A null
        result is the missing-GRN case the match must flag, not an error."""
        return _get_grn(ctx, po_number)

    @tool("post_erp_action", args_schema=PostErpActionArgs)
    def post_erp_action(
        idempotency_key: str,
        doc_number: str,
        vendor_id: str,
        txn_date: date,
        total_amount: str,
        currency: str,
        rationale: str,
        gl_account: str | None = None,
        po_number: str | None = None,
    ) -> dict[str, object]:
        """Post an approved bill to the ERP. Idempotent per idempotency_key.

        Write. The primary action: records the bill as approved-for-payment. The
        deterministic policy engine must clear this call first, and the ERP
        refuses independently (blocked vendor, control-account coding). This does
        NOT execute payment — money never moves through this system."""
        return _post_erp_action(
            ctx,
            PostErpActionArgs(
                idempotency_key=idempotency_key,
                doc_number=doc_number,
                vendor_id=vendor_id,
                txn_date=txn_date,
                total_amount=total_amount,
                currency=currency,
                gl_account=gl_account,
                po_number=po_number,
                rationale=rationale,
            ),
        )

    @tool("notify_approver", args_schema=NotifyApproverArgs)
    def notify_approver(
        required_tier: str, reason: str, invoice_id: str, amount: str, currency: str
    ) -> dict[str, object]:
        """Escalate an invoice to a human approver at the given DOA tier.

        Write (sends a notification). Used when a check forces review or the
        amount exceeds the touchless ceiling. The in-app review queue is the
        system of record; this delivers the notification to the approver's
        channel."""
        return _notify_approver(
            ctx,
            NotifyApproverArgs(
                required_tier=required_tier,
                reason=reason,
                invoice_id=invoice_id,
                amount=amount,
                currency=currency,
            ),
        )

    tools: list[BaseTool] = [
        parse_document,
        # extract_fields is a create_agent-driven sub-agent, not a leaf tool, so
        # it is not in the leaf toolset; the extraction sub-agent owns it.
        retrieve_policy,
        lookup_precedent,
        get_purchase_order,
        get_goods_receipt,
        post_erp_action,
        notify_approver,
    ]
    if len(tools) > MAX_TOOLS:  # pragma: no cover - guarded by a test too
        raise AssertionError(
            f"Toolset has {len(tools)} tools, exceeding the {MAX_TOOLS} cap. "
            "A growing toolset is growing attack surface; keep it narrow."
        )
    return tools


# Stable tool-name groups the RBAC filter uses to scope each sub-agent. Defined
# here (beside the tools) so the names cannot drift from the definitions.
READ_ONLY_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"parse_document", "retrieve_policy", "lookup_precedent", "get_purchase_order", "get_goods_receipt"}
)
WRITE_TOOL_NAMES: Final[frozenset[str]] = frozenset({"post_erp_action", "notify_approver"})
ALL_TOOL_NAMES: Final[frozenset[str]] = READ_ONLY_TOOL_NAMES | WRITE_TOOL_NAMES
