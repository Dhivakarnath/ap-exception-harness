"""Threshold-avoidance detection: `threshold_avoidance` (FR-4.8).

Flags an amount parked just below a DOA approval boundary — a documented
intake fraud pattern: structuring an invoice to land under the amount that
would otherwise require a second set of eyes. `DOAMatrix.boundaries()` is the
exact set of amounts this check watches, so a tenant's own delegation ladder
*is* the fraud-detection surface, with no separate threshold list to keep in
sync.

This is a `FLAG`, never a `FAIL`: sitting just under a boundary is not itself
proof of anything — many genuinely correct invoices happen to land there — so
this check raises attention rather than blocking on its own. It is
`new_vendor` (`policy.identity`) sitting alongside it that turns a single weak
signal into a combination worth a human's attention, exactly as the
adversarial fixture for this failure mode is built to demonstrate.
"""

from __future__ import annotations

from decimal import Decimal

from ap_agent.core.checks import CheckCategory, CheckResult, Severity, Verdict
from ap_agent.policy.context import PolicyEvaluationContext

THRESHOLD_AVOIDANCE = "threshold_avoidance"


def threshold_avoidance(context: PolicyEvaluationContext) -> CheckResult:
    invoice = context.invoice
    pack = context.policy
    amount = invoice.total_amount.value

    if amount.currency != pack.base_currency:
        # Boundaries are denominated in the tenant's base currency; a
        # foreign-currency invoice cannot be meaningfully compared against
        # them without a conversion this engine does not perform.
        return CheckResult(
            name=THRESHOLD_AVOIDANCE,
            category=CheckCategory.FRAUD,
            verdict=Verdict.SKIP,
            reasoning=f"Amount is in {amount.currency}, not the tenant's base "
            f"currency {pack.base_currency}; boundaries are not comparable.",
            threshold="n/a",
            actual=amount.currency,
        )

    band_pct = pack.thresholds.threshold_avoidance_band_pct
    boundaries = pack.doa.boundaries()

    for boundary in boundaries:
        if amount.amount > boundary:
            continue
        gap_pct = ((boundary - amount.amount) / boundary) * Decimal(100)
        if gap_pct <= band_pct:
            reasoning = (
                f"Total {amount} sits just below the {boundary} DOA boundary "
                f"({gap_pct:.2f}% under, within the {band_pct}% watch band)."
            )
            return CheckResult(
                name=THRESHOLD_AVOIDANCE,
                category=CheckCategory.FRAUD,
                verdict=Verdict.FLAG,
                severity=Severity.MEDIUM,
                reasoning=reasoning,
                threshold=f"within {band_pct}% below a DOA boundary",
                actual=f"{gap_pct:.2f}% below {boundary}",
                inputs={"amount": str(amount.amount), "boundary": str(boundary)},
                forces_review=True,
            )
        # Boundaries are ascending (enforced by `DOAMatrix`); the first one
        # the amount sits at or below is the only one relevant to check.
        break

    return CheckResult(
        name=THRESHOLD_AVOIDANCE,
        category=CheckCategory.FRAUD,
        verdict=Verdict.PASS,
        reasoning=f"Total {amount} does not sit suspiciously close beneath any "
        "DOA boundary.",
        threshold=f"within {band_pct}% below a DOA boundary",
        actual=str(amount.amount),
        inputs={"boundaries": [str(b) for b in boundaries]},
    )
