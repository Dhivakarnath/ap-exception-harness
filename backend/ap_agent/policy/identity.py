"""Vendor identity checks: `vendor_resolution`, `vendor_active`,
`bank_detail_change`, and `new_vendor` (FR-4.5, FR-4.6).

Identity resolution happens upstream (`mapping.vendors.resolve_vendor`, built
in the previous slice) and its result — `ALIAS_MATCH`/`RESOLVED`/`AMBIGUOUS`/
`NOT_FOUND` — arrives on the context already decided. `vendor_resolution` is
this package's report on *that* outcome: an ambiguous or unresolved identity
is itself a finding that must escalate, distinct from and prior to
`vendor_active`, which has nothing to check without a resolved vendor to
check the status of.

**`bank_detail_change` always forces review regardless of amount (FR-4.6).**
Altered remit-to details on an otherwise-valid invoice is one of the most
common and costly AP fraud patterns — the invoice content is often completely
correct, only the payment destination has been redirected — so this is the
one check in the engine with no tolerance band and no amount at which it
stops mattering.
"""

from __future__ import annotations

from ap_agent.core.canonical import VendorStatus
from ap_agent.core.checks import CheckCategory, CheckResult, Severity, Verdict
from ap_agent.mapping.vendors import ResolutionOutcome
from ap_agent.policy.context import PolicyEvaluationContext

VENDOR_RESOLUTION = "vendor_resolution"
VENDOR_ACTIVE = "vendor_active"
BANK_DETAIL_CHANGE = "bank_detail_change"
NEW_VENDOR = "new_vendor"


def vendor_resolution(context: PolicyEvaluationContext) -> CheckResult:
    """Whether the printed vendor name resolved to exactly one vendor.

    `AMBIGUOUS` and `NOT_FOUND` are legitimate, common outcomes — not engine
    failures — and both must escalate: paying against a guessed identity
    risks paying the wrong company's bank account (`AMBIGUOUS`), or means
    there is no vendor master to validate anything against at all
    (`NOT_FOUND`, until a human onboards one).
    """
    resolution = context.vendor_resolution
    invoice_name = context.invoice.vendor_name.value

    if resolution is None:
        # No resolution was attempted at all (e.g. a direct `Vendor` was
        # supplied, bypassing the mapping layer entirely — a legitimate path
        # for tests and for callers with their own resolution). Nothing to
        # flag; `vendor_active` is where a supplied vendor's status is
        # judged.
        return CheckResult(
            name=VENDOR_RESOLUTION,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.SKIP,
            reasoning="Vendor resolution was not run for this invoice; a vendor "
            "record was supplied directly.",
            threshold="resolution outcome is ALIAS_MATCH or RESOLVED",
            actual="not run",
        )

    if resolution.outcome in (ResolutionOutcome.ALIAS_MATCH, ResolutionOutcome.RESOLVED):
        return CheckResult(
            name=VENDOR_RESOLUTION,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.PASS,
            reasoning=resolution.reasoning
            or f"{invoice_name!r} resolved to a single vendor.",
            threshold="resolution outcome is ALIAS_MATCH or RESOLVED",
            actual=resolution.outcome.value,
            inputs={"vendor_id": resolution.vendor_id},
        )

    if resolution.outcome is ResolutionOutcome.AMBIGUOUS:
        candidate_names = ", ".join(
            f"{c.legal_name!r} ({c.score:.2f})" for c in resolution.candidates
        )
        return CheckResult(
            name=VENDOR_RESOLUTION,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.FLAG,
            severity=Severity.HIGH,
            reasoning=resolution.reasoning
            or f"{invoice_name!r} matches more than one vendor.",
            threshold="resolution outcome is ALIAS_MATCH or RESOLVED",
            actual=f"AMBIGUOUS: {candidate_names}",
            inputs={
                "printed_name": resolution.printed_name,
                "candidates": [c.vendor_id for c in resolution.candidates],
            },
            forces_review=True,
        )

    # NOT_FOUND
    return CheckResult(
        name=VENDOR_RESOLUTION,
        category=CheckCategory.IDENTITY,
        verdict=Verdict.FLAG,
        severity=Severity.MEDIUM,
        reasoning=resolution.reasoning
        or f"{invoice_name!r} does not match any known vendor.",
        threshold="resolution outcome is ALIAS_MATCH or RESOLVED",
        actual="NOT_FOUND",
        inputs={"printed_name": resolution.printed_name},
        forces_review=True,
    )


def vendor_active(context: PolicyEvaluationContext) -> CheckResult:
    """The resolved vendor is neither inactive nor blocked.

    `Vendor.is_payable` on the canonical model only distinguishes ACTIVE from
    not; this check reports *which* non-active state applies, because the two
    warrant different routes downstream — a blocked vendor (fraud
    investigation) has nothing to wait for and should reject, while an
    inactive vendor (deactivated, possibly reactivatable) should hold.
    """
    vendor = context.vendor

    if vendor is None:
        return CheckResult(
            name=VENDOR_ACTIVE,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.SKIP,
            reasoning="No resolved vendor to check the status of.",
            threshold="status is active",
            actual="no vendor resolved",
        )

    if vendor.status is VendorStatus.BLOCKED:
        return CheckResult(
            name=VENDOR_ACTIVE,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.FAIL,
            severity=Severity.CRITICAL,
            reasoning="Vendor is blocked pending investigation.",
            threshold="status is active",
            actual=VendorStatus.BLOCKED.value,
            inputs={"vendor_id": vendor.vendor_id, "status": vendor.status.value},
            forces_review=True,
        )

    if vendor.status is VendorStatus.INACTIVE:
        return CheckResult(
            name=VENDOR_ACTIVE,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.FAIL,
            severity=Severity.HIGH,
            reasoning="Vendor record is inactive.",
            threshold="status is active",
            actual=VendorStatus.INACTIVE.value,
            inputs={"vendor_id": vendor.vendor_id, "status": vendor.status.value},
            forces_review=True,
        )

    return CheckResult(
        name=VENDOR_ACTIVE,
        category=CheckCategory.IDENTITY,
        verdict=Verdict.PASS,
        reasoning="Vendor is active.",
        threshold="status is active",
        actual=VendorStatus.ACTIVE.value,
        inputs={"vendor_id": vendor.vendor_id},
    )


def bank_detail_change(context: PolicyEvaluationContext) -> CheckResult:
    """Remit-to bank details on the invoice differ from the vendor master.

    Compares fingerprints (`BankDetails.fingerprint()`), never raw account
    numbers — the canonical model retains only the last four digits by design,
    which is all this check needs: detecting a change, not identifying the
    new account. Amount is irrelevant here on purpose; there is no `if amount
    is small, ignore it` branch, because the size of the invoice has no
    bearing on whether the payment destination has been tampered with.
    """
    remit_to = context.invoice.remit_to
    vendor = context.vendor

    if remit_to is None:
        return CheckResult(
            name=BANK_DETAIL_CHANGE,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.SKIP,
            reasoning="Invoice does not state remit-to bank details.",
            threshold="fingerprint matches vendor master",
            actual="not stated",
        )

    if vendor is None or vendor.bank_details is None:
        # No master record to compare against. Not itself a finding for this
        # check — a new or unresolved vendor has no baseline yet — but it is
        # not silently PASS either, since "nothing to compare" and "compared
        # and matched" are different facts a reviewer should be able to tell
        # apart.
        return CheckResult(
            name=BANK_DETAIL_CHANGE,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.SKIP,
            reasoning="No vendor master bank details to compare against.",
            threshold="fingerprint matches vendor master",
            actual="no master record",
        )

    printed_fingerprint = remit_to.value.fingerprint()
    master_fingerprint = vendor.bank_details.fingerprint()

    if printed_fingerprint != master_fingerprint:
        printed_last4 = remit_to.value.account_number_last4 or "????"
        master_last4 = vendor.bank_details.account_number_last4 or "????"
        reasoning = (
            f"Remit-to details differ from the vendor master: invoice ends "
            f"{printed_last4}, master holds {master_last4}."
        )
        return CheckResult(
            name=BANK_DETAIL_CHANGE,
            category=CheckCategory.IDENTITY,
            verdict=Verdict.FLAG,
            severity=Severity.CRITICAL,
            reasoning=reasoning,
            threshold="fingerprint matches vendor master",
            actual=f"printed ends {printed_last4}, master ends {master_last4}",
            inputs={"vendor_id": vendor.vendor_id},
            forces_review=True,
        )

    return CheckResult(
        name=BANK_DETAIL_CHANGE,
        category=CheckCategory.IDENTITY,
        verdict=Verdict.PASS,
        reasoning="Remit-to details match the vendor master.",
        threshold="fingerprint matches vendor master",
        actual="matches",
        inputs={"vendor_id": vendor.vendor_id},
    )


def new_vendor(context: PolicyEvaluationContext) -> CheckResult:
    """The vendor was created shortly before this invoice.

    A documented AP intake fraud signal on its own — a first-time or
    barely-known payee warrants more scrutiny than an established one — and
    materially sharper when it co-occurs with `threshold_avoidance`
    (`policy.fraud`): individually weak signals that are far stronger
    together, exactly as `build_threshold_avoidance` in the adversarial
    fixture set is constructed to exercise.
    """
    vendor = context.vendor
    if vendor is None or vendor.first_seen_at is None:
        return CheckResult(
            name=NEW_VENDOR,
            category=CheckCategory.FRAUD,
            verdict=Verdict.SKIP,
            reasoning="No vendor-creation date available to evaluate.",
            threshold=f"first seen >= {context.policy.thresholds.new_vendor_days} days ago",
            actual="unknown",
        )

    invoice_date = context.invoice.invoice_date.value
    age_days = (invoice_date - vendor.first_seen_at.date()).days
    band = context.policy.thresholds.new_vendor_days

    if age_days < band:
        reasoning = (
            f"Vendor {vendor.name!r} was first seen {age_days} day(s) before "
            f"this invoice, inside the {band}-day new-vendor window."
        )
        return CheckResult(
            name=NEW_VENDOR,
            category=CheckCategory.FRAUD,
            verdict=Verdict.FLAG,
            severity=Severity.MEDIUM,
            reasoning=reasoning,
            threshold=f"first seen >= {band} days ago",
            actual=f"{age_days} days ago",
            inputs={"vendor_id": vendor.vendor_id, "age_days": age_days},
            forces_review=True,
        )

    return CheckResult(
        name=NEW_VENDOR,
        category=CheckCategory.FRAUD,
        verdict=Verdict.PASS,
        reasoning=f"Vendor {vendor.name!r} was established {age_days} days before "
        "this invoice.",
        threshold=f"first seen >= {band} days ago",
        actual=f"{age_days} days ago",
        inputs={"vendor_id": vendor.vendor_id, "age_days": age_days},
    )
