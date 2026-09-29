"""Field-label alias learning: propose once, confirm once, apply forever
(FR-3.1, FR-3.2).

An extraction produces `RecoveredLabel`s (see `mapping.labels`) attributing a
vendor's printed wording — "Grand Total", "Please Remit" — to a canonical
field. Read once, that is a fact about one invoice. Confirmed by a human, it
becomes a fact about the vendor's document format, and every later invoice from
that vendor should apply it without asking a model to re-infer it.

This module is the persistence and lookup half of that loop:

1. `propose` turns this run's `RecoveredLabel`s into candidate rows — not yet
   trusted, not yet applied to anything.
2. A human reviews the proposals (the review surface is v1's HITL screen; this
   module only prepares what it shows) and confirms the ones that are correct.
3. `confirm` persists a confirmed proposal as a `FieldAlias` row.
4. `lookup` resolves a printed label back to a canonical field on a later
   invoice, deterministically — no scoring, no model call.

The one-time cost is a human glance at a handful of labels; the payoff is that
extraction for a repeat vendor stops depending on the model reading the label
correctly every single time.

**Confidence in the proposal is not confidence in the fact.** A proposal
recovered `via same_region` at 0.95 is a strong candidate for confirmation; one
recovered `via model_reported` at 0.65 is worth showing to a human but not
worth auto-confirming. This module never auto-confirms — FR-3.2 requires the
human step unconditionally, regardless of how confident the proposal looked.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ap_agent.mapping.labels import RecoveredLabel, normalise_label_text
from ap_agent.persistence.models import FieldAlias

# Canonical fields alias learning applies to. Deliberately the header fields
# that carry a printed label on the document — not `currency` or `vendor_name`,
# which are typically read from context rather than a labelled key-value pair,
# and not line-item fields, which are positional (a table column) rather than
# label-driven.
ALIASABLE_FIELDS: frozenset[str] = frozenset(
    {
        "invoice_number",
        "invoice_date",
        "due_date",
        "subtotal",
        "tax_amount",
        "total_amount",
        "po_reference",
        "payment_terms",
    }
)


class AliasProposal(BaseModel):
    """One label -> field candidate awaiting human confirmation."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    vendor_id: str | None
    """None means tenant-wide: this vendor is unresolved, or the label pattern
    is being proposed independent of vendor (e.g. a shared template)."""
    label_raw: str
    label_norm: str
    canonical_field: str
    source: str
    """`RecoveredLabel.source` value, so the reviewer sees how confident the
    attribution was, not just the label text."""
    attribution_confidence: float = Field(ge=0.0, le=1.0)
    already_confirmed: bool = False
    """True if an alias already exists for this (tenant, vendor, label). The
    proposal is still returned — a human may want to see it re-affirmed or
    corrected — but the caller should not treat it as new."""


def propose(
    session: Session,
    *,
    tenant_id: str,
    vendor_id: str | None,
    labels: dict[str, RecoveredLabel],
) -> list[AliasProposal]:
    """Build alias proposals from one extraction's recovered labels.

    Only fields in `ALIASABLE_FIELDS` are proposed. Checks each against any
    existing alias so the caller (and reviewer) can see what is genuinely new
    versus already known.
    """
    proposals: list[AliasProposal] = []
    for field, label in labels.items():
        if field not in ALIASABLE_FIELDS:
            continue

        norm = label.normalised
        if not norm:
            continue

        existing = _lookup_row(session, tenant_id=tenant_id, vendor_id=vendor_id, label_norm=norm)
        proposals.append(
            AliasProposal(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
                label_raw=label.text,
                label_norm=norm,
                canonical_field=field,
                source=label.source.value,
                attribution_confidence=label.confidence,
                already_confirmed=existing is not None,
            )
        )
    return proposals


def _lookup_row(
    session: Session, *, tenant_id: str, vendor_id: str | None, label_norm: str
) -> FieldAlias | None:
    query = session.query(FieldAlias).filter(
        FieldAlias.tenant_id == tenant_id, FieldAlias.label_norm == label_norm
    )
    if vendor_id is not None:
        # A vendor-specific rule takes precedence, but a tenant-wide rule
        # (vendor_id NULL) still matches the same label. Prefer the specific
        # one when both exist.
        row = query.filter(FieldAlias.vendor_id == vendor_id).one_or_none()
        if row is not None:
            return row
        return query.filter(FieldAlias.vendor_id.is_(None)).one_or_none()
    return query.filter(FieldAlias.vendor_id.is_(None)).one_or_none()


def confirm(
    session: Session,
    proposal: AliasProposal,
    *,
    confirmed_by: str,
) -> FieldAlias:
    """Persist a human-confirmed proposal as a deterministic rule.

    The only path that creates or updates a `FieldAlias` row. There is no
    "auto-confirm" entry point in this module by design (FR-3.2): every rule
    that will later bypass the model must have had a named human behind it.
    """
    existing = _lookup_row(
        session,
        tenant_id=proposal.tenant_id,
        vendor_id=proposal.vendor_id,
        label_norm=proposal.label_norm,
    )
    now = datetime.now(UTC)

    if existing is not None:
        existing.canonical_field = proposal.canonical_field
        existing.confirmed_by = confirmed_by
        existing.confirmed_at = now
        return existing

    row = FieldAlias(
        tenant_id=proposal.tenant_id,
        vendor_id=proposal.vendor_id,
        label_raw=proposal.label_raw,
        label_norm=proposal.label_norm,
        canonical_field=proposal.canonical_field,
        confirmed_by=confirmed_by,
        confirmed_at=now,
        times_applied=0,
    )
    session.add(row)
    return row


def lookup(
    session: Session,
    *,
    tenant_id: str,
    vendor_id: str | None,
    label: str,
) -> str | None:
    """Resolve a printed label to a canonical field via a confirmed rule.

    Deterministic and exact — normalises the same way `propose` does, so a
    label confirmed once matches every later invoice that prints it
    identically (modulo case and punctuation), without a model in the loop.

    Increments `times_applied` on a hit, purely as a usage signal for
    reviewing which rules are actually earning their keep; it does not affect
    the lookup result.
    """
    norm = normalise_label_text(label)
    row = _lookup_row(session, tenant_id=tenant_id, vendor_id=vendor_id, label_norm=norm)
    if row is None:
        return None
    row.times_applied += 1
    return row.canonical_field


__all__ = [
    "ALIASABLE_FIELDS",
    "AliasProposal",
    "confirm",
    "lookup",
    "propose",
]
