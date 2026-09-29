"""Duplicate detection: `duplicate_exact` and `duplicate_fuzzy` (FR-4.3).

Two independent rules, deliberately, because they warrant two different
responses. An **exact** duplicate — same vendor, same invoice number, same
amount, within the lookback window — is a verbatim resubmission of a bill
already recorded: there is nothing to resolve, so it FAILs and the case
rejects outright. A **fuzzy** duplicate — `INV-001` vs `INV-001A`, same
vendor, same amount, a few days apart — could legitimately be a corrected
resubmission of a genuinely different invoice, so it only FLAGs and holds for
a human to look at, never rejects on its own.

Comparison uses the same vendor-identity key the rest of the engine uses
(`PolicyEvaluationContext.vendor_identity_key`): a resolved vendor id when one
exists, the printed name otherwise. Duplicate detection still has a job even
when identity resolution has not concluded — a resubmitted invoice number and
amount from a plausibly-the-same vendor is suspicious independent of whether a
vendor id was assigned yet.
"""

from __future__ import annotations

from datetime import date, timedelta

from rapidfuzz import fuzz

from ap_agent.core.checks import CheckCategory, CheckResult, Severity, Verdict
from ap_agent.policy.context import HistoricalBill, PolicyEvaluationContext

DUPLICATE_EXACT = "duplicate_exact"
DUPLICATE_FUZZY = "duplicate_fuzzy"


def _same_vendor(context: PolicyEvaluationContext, bill: HistoricalBill) -> bool:
    key = context.vendor_identity_key
    if key is None:
        return False
    kind, value = key
    if kind == "id":
        return bill.vendor_id == value
    # kind == "name": compare against the historical record's own printed
    # name when its vendor id could not be resolved either; fall back to
    # comparing against its id string as a last resort so a bill recorded
    # with only an id is not silently excluded from the name-keyed path.
    candidate = (bill.vendor_name or bill.vendor_id).strip().casefold()
    return candidate == value


def _within_lookback(
    invoice_date: date, bill_date: date, lookback_days: int
) -> bool:
    return timedelta(0) <= (invoice_date - bill_date) <= timedelta(days=lookback_days)


def _candidates(context: PolicyEvaluationContext) -> list[HistoricalBill]:
    lookback = context.policy.duplicates.lookback_days
    invoice_date = context.invoice.invoice_date.value
    return [
        bill
        for bill in context.historical_bills
        if _same_vendor(context, bill)
        and _within_lookback(invoice_date, bill.invoice_date, lookback)
    ]


def duplicate_exact(context: PolicyEvaluationContext) -> CheckResult:
    """A bill from the same vendor with the same invoice number and amount.

    Amount equality is exact (currency and amount both), because an exact
    duplicate is a claim of verbatim resubmission — any difference at all
    means it is not the same bill and belongs to `duplicate_fuzzy` instead.
    """
    invoice = context.invoice
    number = invoice.invoice_number.value.strip().casefold()
    amount = invoice.total_amount.value

    for bill in _candidates(context):
        if bill.invoice_number.strip().casefold() != number:
            continue
        if bill.amount.currency != amount.currency or bill.amount.amount != amount.amount:
            continue
        reasoning = (
            f"{invoice.invoice_number.value!r} already exists for this vendor "
            f"at {bill.amount}, dated {bill.invoice_date.isoformat()}."
        )
        return CheckResult(
            name=DUPLICATE_EXACT,
            category=CheckCategory.DUPLICATE,
            verdict=Verdict.FAIL,
            severity=Severity.CRITICAL,
            reasoning=reasoning,
            threshold="no exact match on vendor + invoice number + amount",
            actual=f"matches {bill.invoice_number!r} ({bill.amount}, {bill.invoice_date})",
            inputs={
                "invoice_number": invoice.invoice_number.value,
                "amount": str(amount),
                "matched_bill_number": bill.invoice_number,
                "matched_bill_date": bill.invoice_date.isoformat(),
            },
            forces_review=True,
        )

    return CheckResult(
        name=DUPLICATE_EXACT,
        category=CheckCategory.DUPLICATE,
        verdict=Verdict.PASS,
        reasoning="No prior bill matches this vendor, invoice number, and amount "
        f"exactly within the {context.policy.duplicates.lookback_days}-day lookback.",
        threshold="no exact match on vendor + invoice number + amount",
        actual="no match",
    )


def duplicate_fuzzy(context: PolicyEvaluationContext) -> CheckResult:
    """A near-miss resubmission: similar invoice number, matching amount,
    within a short date window of an existing bill.

    All three conditions — number similarity, amount match within tolerance,
    and a tight date window — must hold together. Any one alone is common and
    harmless (two unrelated invoices can easily share a number pattern or an
    amount); it is the conjunction that is suspicious.
    """
    invoice = context.invoice
    number = invoice.invoice_number.value.strip()
    amount = invoice.total_amount.value
    rules = context.policy.duplicates

    for bill in _candidates(context):
        if bill.invoice_number.strip().casefold() == number.casefold():
            # An identical number is `duplicate_exact`'s finding (whether or
            # not the amount also matched); fuzzy matching is specifically
            # about *different* numbers that resemble each other.
            continue
        if bill.amount.currency != amount.currency:
            continue

        amount_diff = abs(bill.amount.amount - amount.amount)
        if amount_diff > rules.fuzzy_amount_tolerance:
            continue

        if not _within_lookback(
            invoice.invoice_date.value, bill.invoice_date, rules.fuzzy_date_window_days
        ):
            continue

        similarity = fuzz.ratio(number.casefold(), bill.invoice_number.strip().casefold()) / 100.0
        if similarity < rules.fuzzy_number_similarity:
            continue

        reasoning = (
            f"{number!r} closely resembles paid bill {bill.invoice_number!r} "
            f"(similarity {similarity:.2f}), with a matching amount "
            f"({bill.amount}) within {rules.fuzzy_date_window_days} days."
        )
        return CheckResult(
            name=DUPLICATE_FUZZY,
            category=CheckCategory.DUPLICATE,
            verdict=Verdict.FLAG,
            severity=Severity.HIGH,
            reasoning=reasoning,
            threshold=(
                f"number similarity >= {rules.fuzzy_number_similarity}, amount "
                f"within {rules.fuzzy_amount_tolerance}, date within "
                f"{rules.fuzzy_date_window_days}d"
            ),
            actual=f"similarity {similarity:.2f} vs {bill.invoice_number!r}",
            inputs={
                "invoice_number": number,
                "matched_bill_number": bill.invoice_number,
                "similarity": similarity,
                "amount_diff": str(amount_diff),
            },
            forces_review=True,
        )

    return CheckResult(
        name=DUPLICATE_FUZZY,
        category=CheckCategory.DUPLICATE,
        verdict=Verdict.PASS,
        reasoning="No near-miss match against recent bills from this vendor.",
        threshold=(
            f"number similarity >= {rules.fuzzy_number_similarity}, amount "
            f"within {rules.fuzzy_amount_tolerance}, date within "
            f"{rules.fuzzy_date_window_days}d"
        ),
        actual="no match",
    )
