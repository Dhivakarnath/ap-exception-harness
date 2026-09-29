"""Payment terms: `payment_terms` (FR-4.9).

Parses printed terms text into a due date, an optional early-payment discount
deadline, and the discount's annualised value — the number that actually
decides whether capturing it is worthwhile. `2/10 NET30` (2% off if paid
within 10 days, otherwise net 30) is worth roughly 36.7% annualised, which is
a return no treasury desk would leave on the table; that is the whole reason
this check exists rather than just recording the due date.

**Parsing, not inference.** Terms text follows a small number of well-known
conventions (`NET n`, `n/m NET k`, `Due on receipt`, `EOM`) and this module
parses them with regex against that fixed vocabulary. An unrecognised format
falls back to the tenant's configured default (`PaymentTermsDefaults`) and
says so explicitly in the result — it does not guess a due date from
surrounding context, because a wrong due date silently accepted is a missed
or mistimed payment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ap_agent.core.canonical import Invoice
from ap_agent.core.checks import CheckCategory, CheckResult, Severity, Verdict
from ap_agent.core.policy_pack import PaymentTermsDefaults

PAYMENT_TERMS = "payment_terms"

# 360, not 365: the standard early-payment-discount convention (a 360-day
# "banker's year") is what produces the commonly quoted ~36.7% annualised
# figure for 2/10 NET30 — the same figure cited in `policy_pack.py`'s
# `PaymentTermsDefaults` docstring. Using 365 here would make this module's
# output disagree with that figure for no real-world benefit.
_DAYS_PER_YEAR = Decimal(360)

# `NET 30`, `Net30`, `net 45 days` — a plain net term with no discount.
_NET_RE = re.compile(r"\bnet\s*(\d{1,3})\b", re.IGNORECASE)

# `2/10 NET 30`, `2/10, net30` — discount percent / discount days, then net days.
_DISCOUNT_RE = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*/\s*(\d{1,3})\s*,?\s*net\s*(\d{1,3})", re.IGNORECASE
)

_DUE_ON_RECEIPT_RE = re.compile(r"due\s+on\s+receipt|\bcod\b|\bcash\s+on\s+delivery\b", re.IGNORECASE)

_EOM_RE = re.compile(r"\beom\b|end\s+of\s+month", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParsedTerms:
    """The result of parsing one terms string."""

    net_days: int
    discount_pct: Decimal | None = None
    discount_days: int | None = None
    recognised: bool = True

    @property
    def has_discount(self) -> bool:
        return self.discount_pct is not None and self.discount_days is not None

    def due_date(self, invoice_date: date) -> date:
        return invoice_date + timedelta(days=self.net_days)

    def discount_deadline(self, invoice_date: date) -> date | None:
        if self.discount_days is None:
            return None
        return invoice_date + timedelta(days=self.discount_days)

    def annualised_discount_pct(self) -> Decimal | None:
        """The discount's return if captured, annualised.

        The standard early-payment-discount formula: the percent saved,
        scaled up by how many times a year that saving could be repeated —
        the gap between the discount deadline and the net due date is the
        "loan period" being avoided.
        """
        if self.discount_pct is None or self.discount_days is None:
            return None
        period = self.net_days - self.discount_days
        if period <= 0:
            return None
        return (
            (self.discount_pct / (Decimal(100) - self.discount_pct))
            * (_DAYS_PER_YEAR / Decimal(period))
            * Decimal(100)
        )


def parse_terms(text: str | None, defaults: PaymentTermsDefaults) -> ParsedTerms:
    """Parse printed terms text, falling back to the tenant default.

    The fallback is explicit (`recognised=False`) rather than silent: a
    printed terms string this module cannot parse is a data-quality signal
    the check surfaces, not a reason to pretend the default was what was
    printed.
    """
    candidate = (text or "").strip()
    if not candidate:
        return _parse_default(defaults)

    if _DUE_ON_RECEIPT_RE.search(candidate):
        return ParsedTerms(net_days=0)

    if _EOM_RE.search(candidate):
        # End-of-month terms are not a fixed day count; treated as the
        # tenant's default net period since there is no invoice-date-relative
        # day count to compute without knowing the invoice's day-of-month
        # convention the tenant uses, which this module does not have.
        parsed = _parse_default(defaults)
        return ParsedTerms(net_days=parsed.net_days, recognised=False)

    discount_match = _DISCOUNT_RE.search(candidate)
    if discount_match:
        pct, discount_days, net_days = discount_match.groups()
        return ParsedTerms(
            net_days=int(net_days),
            discount_pct=Decimal(pct),
            discount_days=int(discount_days),
        )

    net_match = _NET_RE.search(candidate)
    if net_match:
        return ParsedTerms(net_days=int(net_match.group(1)))

    parsed = _parse_default(defaults)
    return ParsedTerms(net_days=parsed.net_days, recognised=False)


def _parse_default(defaults: PaymentTermsDefaults) -> ParsedTerms:
    # The default itself is expected to be one of the recognised shapes
    # (e.g. "NET30", "2/10 NET30"); parsed the same way rather than hardcoding
    # a second parser, so tenant defaults stay expressed in one place.
    net_match = _NET_RE.search(defaults.default_terms)
    discount_match = _DISCOUNT_RE.search(defaults.default_terms)
    if discount_match:
        pct, discount_days, net_days = discount_match.groups()
        return ParsedTerms(
            net_days=int(net_days), discount_pct=Decimal(pct), discount_days=int(discount_days)
        )
    if net_match:
        return ParsedTerms(net_days=int(net_match.group(1)))
    # The configured default itself does not match a known shape. This is a
    # policy-pack authoring problem, not a per-invoice one, and 30 days is a
    # conservative, clearly-flagged fallback rather than a crash on every
    # invoice for this tenant.
    return ParsedTerms(net_days=30, recognised=False)


def payment_terms(invoice: Invoice, defaults: PaymentTermsDefaults) -> CheckResult:
    """Parse terms, compute the due date and any discount, and report both.

    Always PASSes — this check reports facts (due date, discount value) for
    the payment-scheduling and discount-capture workflow rather than judging
    the invoice; there is no failure condition here, only information. An
    unrecognised terms string is surfaced with `FLAG` because a reviewer
    should know the due date being used is a fallback, not what was printed.
    """
    raw_terms = invoice.payment_terms.value if invoice.payment_terms is not None else None
    parsed = parse_terms(raw_terms, defaults)
    invoice_date = invoice.invoice_date.value
    due = parsed.due_date(invoice_date)

    inputs: dict[str, object] = {
        "printed_terms": raw_terms,
        "recognised": parsed.recognised,
        "due_date": due.isoformat(),
        "net_days": parsed.net_days,
    }

    if parsed.has_discount:
        deadline = parsed.discount_deadline(invoice_date)
        annualised = parsed.annualised_discount_pct()
        inputs |= {
            "discount_pct": str(parsed.discount_pct),
            "discount_deadline": deadline.isoformat() if deadline else None,
            "discount_annualised_pct": str(annualised) if annualised is not None else None,
        }
        worth_capturing = (
            defaults.capture_discounts
            and annualised is not None
            and annualised >= defaults.min_discount_annualised_pct
        )
        if annualised is not None:
            reasoning = (
                f"Due {due.isoformat()}. Early-payment discount of "
                f"{parsed.discount_pct}% available if paid by "
                f"{deadline.isoformat() if deadline else 'n/a'} "
                f"({annualised:.1f}% annualised)."
            )
        else:
            reasoning = (
                f"Due {due.isoformat()}. Discount terms present but the "
                "discount window does not yield a computable annualised rate."
            )
        if worth_capturing:
            reasoning += " Worth capturing under this tenant's policy."
        return CheckResult(
            name=PAYMENT_TERMS,
            category=CheckCategory.TERMS,
            verdict=Verdict.PASS if parsed.recognised else Verdict.FLAG,
            severity=Severity.INFO if parsed.recognised else Severity.LOW,
            reasoning=reasoning,
            threshold=f">= {defaults.min_discount_annualised_pct}% annualised to flag capture",
            actual=f"{annualised:.1f}%" if annualised is not None else "n/a",
            inputs=inputs,
            forces_review=not parsed.recognised,
        )

    reasoning = f"Due {due.isoformat()} ({parsed.net_days} days)."
    if not parsed.recognised:
        reasoning += (
            f" Printed terms {raw_terms!r} were not recognised; used the "
            f"tenant default ({defaults.default_terms})."
        )

    return CheckResult(
        name=PAYMENT_TERMS,
        category=CheckCategory.TERMS,
        verdict=Verdict.PASS if parsed.recognised else Verdict.FLAG,
        severity=Severity.INFO if parsed.recognised else Severity.LOW,
        reasoning=reasoning,
        threshold="terms text recognised",
        actual=raw_terms or "(not stated)",
        inputs=inputs,
        forces_review=not parsed.recognised,
    )
