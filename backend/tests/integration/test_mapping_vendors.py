"""Vendor entity resolution (`mapping.vendors`, FR-3.3).

Marked `integration`: resolution runs pg_trgm's `similarity()` through
Postgres, which the unit tier cannot fake without testing a different
algorithm than the one that actually runs in production.

The load-bearing case throughout is the project's own confusable-pair fixture:
V-1001 "Acme Corporation" and V-1002 "Acme Industries LLC" are two real,
distinct companies (see `apfixtures.spec`), and a name like "Acme Corp" must
resolve to neither — escalating instead of guessing, because guessing risks
paying the wrong company's bank account.
"""

from __future__ import annotations

import pytest

from ap_agent.mapping.vendors import (
    ResolutionOutcome,
    confirm_alias,
    normalise_name,
    resolve_vendor,
)
from ap_agent.persistence.db import session_scope
from ap_agent.persistence.models import Tenant, Vendor, VendorAlias

pytestmark = pytest.mark.integration

TENANT = "vendor-resolution-test-tenant"

VENDOR_ACME = "V-VR-1001"
VENDOR_ACME_INDUSTRIES = "V-VR-1002"
VENDOR_GLOBEX = "V-VR-1003"
VENDOR_BLOCKED = "V-VR-1004"


@pytest.fixture
def tenant() -> str:
    _purge(TENANT)
    with session_scope() as s:
        s.add(
            Tenant(
                tenant_id=TENANT,
                name="Vendor Resolution Test",
                industry="test",
                base_currency="USD",
                active_policy_version="1.0.0",
            )
        )
        s.add_all(
            [
                Vendor(
                    tenant_id=TENANT,
                    vendor_id=VENDOR_ACME,
                    erp_vendor_id="V-1001",
                    legal_name="Acme Corporation",
                ),
                Vendor(
                    tenant_id=TENANT,
                    vendor_id=VENDOR_ACME_INDUSTRIES,
                    erp_vendor_id="V-1002",
                    legal_name="Acme Industries LLC",
                ),
                Vendor(
                    tenant_id=TENANT,
                    vendor_id=VENDOR_GLOBEX,
                    erp_vendor_id="V-1003",
                    legal_name="Globex Industries",
                ),
                Vendor(
                    tenant_id=TENANT,
                    vendor_id=VENDOR_BLOCKED,
                    erp_vendor_id="V-1004",
                    legal_name="Acme Corporation Blocked Twin",
                    status="blocked",
                ),
            ]
        )
    yield TENANT
    _purge(TENANT)


def _purge(tenant_id: str) -> None:
    with session_scope() as s:
        s.query(Tenant).filter_by(tenant_id=tenant_id).delete()


class TestNormaliseName:
    def test_casefolds_and_strips_punctuation(self) -> None:
        assert normalise_name("Acme Corp.") == "acme corp"

    def test_collapses_whitespace(self) -> None:
        assert normalise_name("Acme   Corp") == "acme corp"

    def test_strips_accents(self) -> None:
        assert normalise_name("Café Corp") == "cafe corp"


class TestExactAndCloseMatches:
    def test_exact_legal_name_resolves(self, tenant: str) -> None:
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "Acme Corporation")
        assert result.outcome is ResolutionOutcome.RESOLVED
        assert result.vendor_id == VENDOR_ACME

    def test_minor_punctuation_variant_resolves(self, tenant: str) -> None:
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "Acme Corporation.")
        assert result.outcome is ResolutionOutcome.RESOLVED
        assert result.vendor_id == VENDOR_ACME

    def test_unrelated_vendor_resolves_distinctly(self, tenant: str) -> None:
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "Globex Industries")
        assert result.outcome is ResolutionOutcome.RESOLVED
        assert result.vendor_id == VENDOR_GLOBEX

    def test_blocked_vendor_is_excluded_from_candidates(self, tenant: str) -> None:
        # A blocked vendor's near-identical name must not win resolution —
        # paying it would be exactly the outcome the block exists to prevent.
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "Acme Corporation Blocked Twin")
        assert result.vendor_id != VENDOR_BLOCKED


class TestConfusablePairEscalates:
    """The core assertion: two genuinely distinct vendors sharing a name
    fragment must never be silently merged."""

    @pytest.mark.parametrize(
        "printed_name",
        ["Acme Corp", "ACME", "Acme Co.", "Acme Corp.", "acme corporation ltd"],
    )
    def test_ambiguous_printed_forms_escalate(self, tenant: str, printed_name: str) -> None:
        with session_scope() as s:
            result = resolve_vendor(s, tenant, printed_name)
        assert result.outcome in (
            ResolutionOutcome.AMBIGUOUS,
            ResolutionOutcome.NOT_FOUND,
        ), (
            f"{printed_name!r} must not silently resolve to a single vendor "
            f"when two distinct vendors are both plausible matches; got "
            f"{result.outcome} -> {result.vendor_id}"
        )
        assert result.vendor_id is None

    def test_ambiguous_result_carries_both_candidates(self, tenant: str) -> None:
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "Acme Corp")
        if result.outcome is ResolutionOutcome.AMBIGUOUS:
            candidate_ids = {c.vendor_id for c in result.candidates}
            assert VENDOR_ACME in candidate_ids
            assert VENDOR_ACME_INDUSTRIES in candidate_ids
            assert result.reasoning  # human-readable explanation is present


class TestNoCandidate:
    def test_wholly_unrelated_name_is_not_found(self, tenant: str) -> None:
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "Zylophone Quasar Widgets")
        assert result.outcome is ResolutionOutcome.NOT_FOUND
        assert result.vendor_id is None

    def test_empty_name_is_not_found(self, tenant: str) -> None:
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "   ")
        assert result.outcome is ResolutionOutcome.NOT_FOUND


class TestConfirmedAliasIsDeterministic:
    def test_confirmed_alias_resolves_without_scoring(self, tenant: str) -> None:
        with session_scope() as s:
            confirm_alias(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ACME_INDUSTRIES,
                printed_name="Acme Corp",
                confirmed_by="reviewer@example.com",
            )

        # The same string that would otherwise escalate now resolves exactly,
        # and to the vendor a human confirmed — not whichever scored higher.
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "Acme Corp")
        assert result.outcome is ResolutionOutcome.ALIAS_MATCH
        assert result.vendor_id == VENDOR_ACME_INDUSTRIES
        assert result.candidates == ()  # scoring never ran

    def test_alias_lookup_is_case_and_punctuation_insensitive(self, tenant: str) -> None:
        with session_scope() as s:
            confirm_alias(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ACME,
                printed_name="ACME",
                confirmed_by="reviewer@example.com",
            )
        with session_scope() as s:
            result = resolve_vendor(s, tenant, "acme")
        assert result.outcome is ResolutionOutcome.ALIAS_MATCH
        assert result.vendor_id == VENDOR_ACME

    def test_reconfirming_the_same_name_updates_rather_than_duplicates(
        self, tenant: str
    ) -> None:
        with session_scope() as s:
            confirm_alias(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ACME,
                printed_name="Acme Co.",
                confirmed_by="first-reviewer@example.com",
            )
        with session_scope() as s:
            confirm_alias(
                s,
                tenant_id=tenant,
                vendor_id=VENDOR_ACME_INDUSTRIES,
                printed_name="Acme Co.",
                confirmed_by="second-reviewer@example.com",
            )
        with session_scope() as s:
            rows = (
                s.query(VendorAlias)
                .filter_by(tenant_id=tenant, alias_norm="acme co")
                .all()
            )
        assert len(rows) == 1
        assert rows[0].vendor_id == VENDOR_ACME_INDUSTRIES
        assert rows[0].confirmed_by == "second-reviewer@example.com"

    def test_alias_is_scoped_per_tenant(self, tenant: str) -> None:
        other = "vendor-resolution-test-tenant-2"
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
            s.add(
                Vendor(
                    tenant_id=other,
                    vendor_id="V-OTHER-1",
                    erp_vendor_id="V-1",
                    legal_name="Acme Corporation",
                )
            )

        try:
            with session_scope() as s:
                confirm_alias(
                    s,
                    tenant_id=tenant,
                    vendor_id=VENDOR_ACME_INDUSTRIES,
                    printed_name="Acme Corp",
                    confirmed_by="reviewer@example.com",
                )
            with session_scope() as s:
                other_result = resolve_vendor(s, other, "Acme Corp")
            # The other tenant has no such alias; its own resolution runs
            # independently and must not see the first tenant's confirmation.
            assert other_result.outcome != ResolutionOutcome.ALIAS_MATCH
        finally:
            _purge(other)
