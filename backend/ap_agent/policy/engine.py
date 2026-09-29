"""The orchestrator: runs every rule and returns an ordered `CheckLedger`.

Evaluation order is fixed and deliberate, not alphabetical and not
configurable per tenant — it mirrors the order a human reviewer would
naturally reason in, and it is the order the UI renders the ledger in (design
§11). Document integrity and arithmetic come first because nothing else is
worth evaluating on a document that is not even internally coherent; identity
and duplicate risk come next because they are the highest-cost failure modes
(paying twice, paying a blocked vendor, paying a redirected account);
matching, fraud signals, terms, and authority follow, each building on facts
the earlier checks already established.

**No rule evaluator invokes a model.** This is the property `test_no_rule_
evaluator_invokes_a_model` in the test suite asserts directly, and it is the
whole reason this package exists as pure functions rather than prompts: an
exact arithmetic reconciliation or a duplicate-invoice-number lookup has one
correct answer, and putting either behind a model would introduce
non-determinism into a decision an audit trail must be able to replay
byte-for-byte.

**One evaluator's exception does not silently blank the ledger.** Any
individual check function may raise (a genuinely malformed context, an
unsupported currency reaching arithmetic it cannot perform) and `evaluate_all`
lets it propagate rather than catching-and-continuing — a policy engine that
swallowed its own evaluation errors and returned a partial ledger would be
indistinguishable from one that quietly skipped a control, which is exactly
the failure mode this whole project is built to avoid (FR-8.3).
"""

from __future__ import annotations

from typing import Final

from ap_agent.core.checks import CheckLedger, CheckResult
from ap_agent.policy.arithmetic import CURRENCY_CONSISTENCY, MATH_INTEGRITY
from ap_agent.policy.arithmetic import currency_consistency as _currency_consistency
from ap_agent.policy.arithmetic import math_integrity as _math_integrity
from ap_agent.policy.authority import DOA_ROUTE, SOD_CHECK
from ap_agent.policy.authority import doa_route as _doa_route
from ap_agent.policy.authority import sod_check as _sod_check
from ap_agent.policy.context import PolicyEvaluationContext
from ap_agent.policy.document import CHECK_NAME as COMPLETENESS
from ap_agent.policy.document import completeness as _completeness
from ap_agent.policy.duplicates import DUPLICATE_EXACT, DUPLICATE_FUZZY
from ap_agent.policy.duplicates import duplicate_exact as _duplicate_exact
from ap_agent.policy.duplicates import duplicate_fuzzy as _duplicate_fuzzy
from ap_agent.policy.fraud import THRESHOLD_AVOIDANCE
from ap_agent.policy.fraud import threshold_avoidance as _threshold_avoidance
from ap_agent.policy.identity import (
    BANK_DETAIL_CHANGE,
    NEW_VENDOR,
    VENDOR_ACTIVE,
    VENDOR_RESOLUTION,
)
from ap_agent.policy.identity import bank_detail_change as _bank_detail_change
from ap_agent.policy.identity import new_vendor as _new_vendor
from ap_agent.policy.identity import vendor_active as _vendor_active
from ap_agent.policy.identity import vendor_resolution as _vendor_resolution
from ap_agent.policy.matching import THREE_WAY_MATCH
from ap_agent.policy.matching import three_way_match as _three_way_match
from ap_agent.policy.terms import PAYMENT_TERMS
from ap_agent.policy.terms import payment_terms as _payment_terms

# Fixed evaluation order. See module docstring for the rationale; changing
# this tuple changes what the ledger looks like in the UI and is therefore a
# considered decision, not a refactor.
CHECK_NAMES: Final[tuple[str, ...]] = (
    COMPLETENESS,
    MATH_INTEGRITY,
    CURRENCY_CONSISTENCY,
    DUPLICATE_EXACT,
    DUPLICATE_FUZZY,
    VENDOR_RESOLUTION,
    VENDOR_ACTIVE,
    BANK_DETAIL_CHANGE,
    NEW_VENDOR,
    THREE_WAY_MATCH,
    THRESHOLD_AVOIDANCE,
    PAYMENT_TERMS,
    DOA_ROUTE,
    SOD_CHECK,
)


def evaluate_all(context: PolicyEvaluationContext) -> CheckLedger:
    """Run every deterministic rule against `context`, in fixed order."""
    results: list[CheckResult] = [
        _completeness(context.invoice),
        _math_integrity(context.invoice, context.policy.tolerances),
        _currency_consistency(context.invoice),
        _duplicate_exact(context),
        _duplicate_fuzzy(context),
        _vendor_resolution(context),
        _vendor_active(context),
        _bank_detail_change(context),
        _new_vendor(context),
        _three_way_match(context),
        _threshold_avoidance(context),
        _payment_terms(context.invoice, context.policy.payment_terms),
        _doa_route(context),
        _sod_check(context),
    ]
    return CheckLedger(results=tuple(results))
