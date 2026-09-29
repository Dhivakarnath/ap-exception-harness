"""Field-label alias learning (`mapping.aliases`, FR-3.1/FR-3.2).

Marked `integration`: persistence and the unique-constraint-driven upsert
behaviour are exactly what is under test, so this must run against real
Postgres rather than a mock session.

The property that matters most: nothing in this module can create a
`FieldAlias` row except `confirm`, called with an explicit `confirmed_by`.
There is no path from a `RecoveredLabel`, however confident, straight to a
persisted rule — FR-3.2 requires the human step unconditionally.
"""

from __future__ import annotations

import pytest

from ap_agent.mapping.aliases import ALIASABLE_FIELDS, confirm, lookup, propose
from ap_agent.mapping.labels import LabelSource, RecoveredLabel
from ap_agent.persistence.db import session_scope
from ap_agent.persistence.models import FieldAlias, Tenant, Vendor

pytestmark = pytest.mark.integration

TENANT = "field-alias-test-tenant"
VENDOR_ID = "V-FA-1001"


@pytest.fixture
def tenant() -> str:
    _purge(TENANT)
    with session_scope() as s:
        s.add(
            Tenant(
                tenant_id=TENANT,
                name="Field Alias Test",
                industry="test",
                base_currency="USD",
                active_policy_version="1.0.0",
            )
        )
        s.add(
            Vendor(
                tenant_id=TENANT,
                vendor_id=VENDOR_ID,
                erp_vendor_id="V-1001",
                legal_name="Acme Corporation",
            )
        )
    yield TENANT
    _purge(TENANT)


def _purge(tenant_id: str) -> None:
    with session_scope() as s:
        s.query(Tenant).filter_by(tenant_id=tenant_id).delete()


def _label(text: str, *, source: LabelSource = LabelSource.SAME_REGION, confidence: float = 0.9) -> RecoveredLabel:
    return RecoveredLabel(text=text, source=source, confidence=confidence)


class TestPropose:
    def test_only_aliasable_fields_are_proposed(self, tenant: str) -> None:
        labels = {
            "invoice_number": _label("Invoice Number:"),
            "vendor_name": _label("Acme Corporation"),  # not aliasable
            "subtotal": _label("Subtotal:"),
        }
        with session_scope() as s:
            proposals = propose(s, tenant_id=tenant, vendor_id=VENDOR_ID, labels=labels)

        fields = {p.canonical_field for p in proposals}
        assert fields == {"invoice_number", "subtotal"}
        assert "vendor_name" not in fields

    def test_proposal_carries_attribution_metadata(self, tenant: str) -> None:
        labels = {"total_amount": _label("Grand Total:", source=LabelSource.MODEL_REPORTED, confidence=0.65)}
        with session_scope() as s:
            proposals = propose(s, tenant_id=tenant, vendor_id=VENDOR_ID, labels=labels)

        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal.label_raw == "Grand Total:"
        assert proposal.label_norm == "grand total"
        assert proposal.source == "model_reported"
        assert proposal.attribution_confidence == 0.65
        assert proposal.already_confirmed is False

    def test_already_confirmed_is_flagged(self, tenant: str) -> None:
        with session_scope() as s:
            proposal = propose(
                s, tenant_id=tenant, vendor_id=VENDOR_ID, labels={"due_date": _label("Due Date:")}
            )[0]
            confirm(s, proposal, confirmed_by="reviewer@example.com")

        with session_scope() as s:
            re_proposed = propose(
                s, tenant_id=tenant, vendor_id=VENDOR_ID, labels={"due_date": _label("Due Date:")}
            )
        assert re_proposed[0].already_confirmed is True

    def test_no_proposals_created_without_confirmation(self, tenant: str) -> None:
        with session_scope() as s:
            propose(
                s, tenant_id=tenant, vendor_id=VENDOR_ID, labels={"due_date": _label("Due Date:")}
            )
        # `propose` alone must never write a FieldAlias row.
        with session_scope() as s:
            rows = s.query(FieldAlias).filter_by(tenant_id=tenant).all()
        assert rows == []


class TestConfirmAndLookup:
    def test_confirmed_alias_is_looked_up_deterministically(self, tenant: str) -> None:
        with session_scope() as s:
            proposal = propose(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ID,
                labels={"total_amount": _label("Grand Total:")},
            )[0]
            confirm(s, proposal, confirmed_by="reviewer@example.com")

        with session_scope() as s:
            field = lookup(s, tenant_id=tenant, vendor_id=VENDOR_ID, label="Grand Total:")
        assert field == "total_amount"

    def test_lookup_is_normalisation_insensitive(self, tenant: str) -> None:
        with session_scope() as s:
            proposal = propose(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ID,
                labels={"total_amount": _label("Grand Total:")},
            )[0]
            confirm(s, proposal, confirmed_by="reviewer@example.com")

        with session_scope() as s:
            field = lookup(s, tenant_id=tenant, vendor_id=VENDOR_ID, label="  grand total  ")
        assert field == "total_amount"

    def test_lookup_returns_none_for_unconfirmed_label(self, tenant: str) -> None:
        with session_scope() as s:
            field = lookup(s, tenant_id=tenant, vendor_id=VENDOR_ID, label="Never Seen Label")
        assert field is None

    def test_vendor_specific_rule_does_not_leak_to_another_vendor(self, tenant: str) -> None:
        other_vendor = "V-FA-9999"
        with session_scope() as s:
            s.add(
                Vendor(
                    tenant_id=tenant,
                    vendor_id=other_vendor,
                    erp_vendor_id="V-9999",
                    legal_name="Other Vendor Inc",
                )
            )
            proposal = propose(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ID,
                labels={"total_amount": _label("Please Remit:")},
            )[0]
            confirm(s, proposal, confirmed_by="reviewer@example.com")

        with session_scope() as s:
            field = lookup(s, tenant_id=tenant, vendor_id=other_vendor, label="Please Remit:")
        assert field is None

    def test_tenant_wide_rule_applies_when_vendor_has_no_specific_rule(
        self, tenant: str
    ) -> None:
        with session_scope() as s:
            proposal = propose(
                s,
                tenant_id=tenant,
                vendor_id=None,  # tenant-wide
                labels={"payment_terms": _label("Settlement Terms:")},
            )[0]
            confirm(s, proposal, confirmed_by="reviewer@example.com")

        with session_scope() as s:
            field = lookup(s, tenant_id=tenant, vendor_id=VENDOR_ID, label="Settlement Terms:")
        assert field == "payment_terms"

    def test_vendor_specific_rule_takes_precedence_over_tenant_wide(
        self, tenant: str
    ) -> None:
        with session_scope() as s:
            tenant_wide = propose(
                s, tenant_id=tenant, vendor_id=None, labels={"total_amount": _label("Amount Due:")}
            )[0]
            confirm(s, tenant_wide, confirmed_by="reviewer@example.com")

            vendor_specific = propose(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ID,
                labels={"due_date": _label("Amount Due:")},
            )[0]
            confirm(s, vendor_specific, confirmed_by="reviewer@example.com")

        with session_scope() as s:
            field = lookup(s, tenant_id=tenant, vendor_id=VENDOR_ID, label="Amount Due:")
        assert field == "due_date"  # the vendor-specific rule, not the tenant-wide one

    def test_reconfirming_updates_the_canonical_field_rather_than_duplicating(
        self, tenant: str
    ) -> None:
        with session_scope() as s:
            first = propose(
                s, tenant_id=tenant, vendor_id=VENDOR_ID, labels={"subtotal": _label("Net Amount:")}
            )[0]
            confirm(s, first, confirmed_by="reviewer-a@example.com")

            corrected = propose(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ID,
                labels={"total_amount": _label("Net Amount:")},
            )[0]
            confirm(s, corrected, confirmed_by="reviewer-b@example.com")

        with session_scope() as s:
            rows = (
                s.query(FieldAlias)
                .filter_by(tenant_id=tenant, vendor_id=VENDOR_ID, label_norm="net amount")
                .all()
            )
        assert len(rows) == 1
        assert rows[0].canonical_field == "total_amount"
        assert rows[0].confirmed_by == "reviewer-b@example.com"

    def test_times_applied_increments_on_each_lookup_hit(self, tenant: str) -> None:
        with session_scope() as s:
            proposal = propose(
                s, tenant_id=tenant, vendor_id=VENDOR_ID, labels={"subtotal": _label("Subtotal:")}
            )[0]
            confirm(s, proposal, confirmed_by="reviewer@example.com")

        with session_scope() as s:
            lookup(s, tenant_id=tenant, vendor_id=VENDOR_ID, label="Subtotal:")
            lookup(s, tenant_id=tenant, vendor_id=VENDOR_ID, label="Subtotal:")

        with session_scope() as s:
            row = (
                s.query(FieldAlias)
                .filter_by(tenant_id=tenant, vendor_id=VENDOR_ID, label_norm="subtotal")
                .one()
            )
        assert row.times_applied == 2

    def test_alias_is_scoped_per_tenant(self, tenant: str) -> None:
        other = "field-alias-test-tenant-2"
        with session_scope() as s:
            s.add(
                Tenant(
                    tenant_id=other,
                    name="Other",
                    industry="test",
                    base_currency="USD",
                    active_policy_version="1.0.0",
                )
            )

        try:
            with session_scope() as s:
                proposal = propose(
                    s,
                    tenant_id=tenant,
                    vendor_id=VENDOR_ID,
                    labels={"total_amount": _label("Grand Total:")},
                )[0]
                confirm(s, proposal, confirmed_by="reviewer@example.com")

            with session_scope() as s:
                field = lookup(s, tenant_id=other, vendor_id=None, label="Grand Total:")
            assert field is None
        finally:
            with session_scope() as s:
                s.query(Tenant).filter_by(tenant_id=other).delete()


class TestAliasableFieldsCoverage:
    def test_all_aliasable_fields_are_header_fields_with_labels(self) -> None:
        # Sanity: the set should not include line-item or context-inferred
        # fields, which are positional/derived rather than label-driven.
        assert "vendor_name" not in ALIASABLE_FIELDS
        assert "currency" not in ALIASABLE_FIELDS
        assert "invoice_number" in ALIASABLE_FIELDS
        assert "total_amount" in ALIASABLE_FIELDS
