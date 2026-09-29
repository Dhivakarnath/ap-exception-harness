"""Arithmetic integrity: `math_integrity` and `currency_consistency` (FR-4.7).

Exactly why `Money` is `Decimal` and refuses float (`core.primitives`): these
checks compare printed sums for **exact** equality, and a binary-float
rounding artefact would either manufacture a false mismatch on a genuinely
correct invoice or hide a real one behind noise. Both outcomes defeat the
point of having this check at all.

Two independent facts are checked and reported as two independent
`CheckResult`s, not one combined verdict — `math_integrity` (do the printed
numbers add up) and `currency_consistency` (is one currency used throughout).
A currency-mismatched invoice whose amounts otherwise add up correctly in
their respective currencies is not an arithmetic defect; conflating the two
would make the reasoning field misleading about which problem is actually
present.
"""

from __future__ import annotations

from ap_agent.core.canonical import Invoice
from ap_agent.core.checks import CheckCategory, CheckResult, Severity, Verdict
from ap_agent.core.policy_pack import Tolerances

MATH_INTEGRITY = "math_integrity"
CURRENCY_CONSISTENCY = "currency_consistency"


def math_integrity(invoice: Invoice, tolerances: Tolerances) -> CheckResult:
    """Line items sum to the printed subtotal; subtotal + tax = total.

    Reconciliation tolerance is `tolerances.total_absolute` — the tenant's own
    configured allowance for rounding differences on a total (manufacturing's
    policy pack sets it to $1.00 "per-line rounding differences only";
    retail's to $5.00 for its looser posture). This is the one place that
    value is actually exercised: it describes *arithmetic* slack, which is
    exactly what this check evaluates, not the three-way-match variance
    against a PO (`policy.matching` deliberately does not re-use it there —
    see that module's docstring for why).

    Both reconciliations are checked and reported together because they are
    the same underlying question — does this document's arithmetic hold — and
    a reviewer benefits from seeing both numbers even when only one is wrong.
    Skips a reconciliation it has no basis to check (no lines, or no printed
    tax) rather than fabricating a comparison against nothing.
    """
    subtotal = invoice.subtotal.value
    total = invoice.total_amount.value
    tax = invoice.tax_amount.value if invoice.tax_amount is not None else None
    tolerance = tolerances.total_absolute

    problems: list[str] = []
    inputs: dict[str, object] = {
        "printed_subtotal": str(subtotal),
        "printed_total": str(total),
        "printed_tax": str(tax) if tax is not None else None,
    }

    line_sum = invoice.computed_line_sum()
    if line_sum is not None:
        inputs["computed_line_sum"] = str(line_sum)
        if line_sum.currency == subtotal.currency:
            diff = line_sum.abs_difference(subtotal)
            inputs["line_sum_vs_subtotal_diff"] = str(diff.amount)
            if diff.amount > tolerance:
                problems.append(
                    f"line items sum to {line_sum} but the printed subtotal is "
                    f"{subtotal}"
                )
        # A currency mismatch between lines and the subtotal is
        # `currency_consistency`'s finding, not this check's — reported there
        # so each problem has exactly one owner.

    if tax is not None and tax.currency == subtotal.currency == total.currency:
        expected_total = subtotal + tax
        diff = expected_total.abs_difference(total)
        inputs["subtotal_plus_tax_vs_total_diff"] = str(diff.amount)
        if diff.amount > tolerance:
            problems.append(
                f"subtotal ({subtotal}) plus tax ({tax}) is {expected_total}, "
                f"but the printed total is {total}"
            )

    if problems:
        reasoning = "Arithmetic does not reconcile: " + "; ".join(problems) + "."
        return CheckResult(
            name=MATH_INTEGRITY,
            category=CheckCategory.ARITHMETIC,
            verdict=Verdict.FAIL,
            severity=Severity.HIGH,
            reasoning=reasoning,
            threshold=f"exact match within {tolerance}",
            actual="; ".join(problems),
            inputs=inputs,
            forces_review=True,
        )

    return CheckResult(
        name=MATH_INTEGRITY,
        category=CheckCategory.ARITHMETIC,
        verdict=Verdict.PASS,
        reasoning="Line items sum to the printed subtotal, and subtotal plus tax "
        "equals the printed total.",
        threshold=f"exact match within {tolerance}",
        actual="reconciles",
        inputs=inputs,
    )


def currency_consistency(invoice: Invoice) -> CheckResult:
    """Exactly one currency is used across header and every line.

    No implicit conversion is ever performed (`Money` refuses cross-currency
    arithmetic outright) — a EUR line total misread as USD is a silent
    mispayment, not a rounding nuisance, so this must FAIL loudly rather than
    let `Money` raise an uncaught exception deeper in the pipeline.
    """
    currencies = invoice.currencies_present()

    if len(currencies) > 1:
        reasoning = (
            f"Invoice mixes currencies: {', '.join(sorted(currencies))}. No "
            "implicit conversion is permitted."
        )
        return CheckResult(
            name=CURRENCY_CONSISTENCY,
            category=CheckCategory.ARITHMETIC,
            verdict=Verdict.FAIL,
            severity=Severity.HIGH,
            reasoning=reasoning,
            threshold="exactly one currency",
            actual=", ".join(sorted(currencies)),
            inputs={"currencies_present": sorted(currencies)},
            forces_review=True,
        )

    # `currencies_present()` always includes the header currency, so this is
    # never empty for a validly constructed `Invoice`.
    only = next(iter(currencies))
    return CheckResult(
        name=CURRENCY_CONSISTENCY,
        category=CheckCategory.ARITHMETIC,
        verdict=Verdict.PASS,
        reasoning=f"Every amount is denominated in {only}.",
        threshold="exactly one currency",
        actual=only,
        inputs={"currencies_present": sorted(currencies)},
    )
