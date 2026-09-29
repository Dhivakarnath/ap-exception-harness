"""Authority and segregation of duties: `doa_route` and `sod_check`
(FR-4.10, FR-4.11).

`doa_route` answers "who may authorise this" — the delegation-of-authority
tier for this amount, from the tenant's own matrix keyed on amount alone in
this v1 (department/entity/GL/project-specific bands are a declared v2
extension the `DOABand.roles` field already anticipates). `sod_check` answers
a narrower, harder-edged question: is the agent itself permitted to be the
final word on this invoice, and — when the identities are known — are the
coder, approver, and payer roles actually held by different people.

**`sod_check` never runs on trust.** `SoDRules.agent_may_approve_above_touchless`
is schema-enforced to always be `False` (`policy_pack.py`'s
`_forbid_unsafe_configuration` — a tenant cannot configure this away even by
mistake), and this check asserts that invariant again at evaluation time
rather than assuming the schema was never bypassed by, say, a hand-constructed
`PolicyPack` in a test or script that skipped validation. Defence in depth
applies to configuration the same as it applies to tool permissions.
"""

from __future__ import annotations

from ap_agent.core.checks import CheckCategory, CheckResult, Severity, Verdict
from ap_agent.errors import PolicyPackInvariantError
from ap_agent.policy.context import PolicyEvaluationContext

DOA_ROUTE = "doa_route"
SOD_CHECK = "sod_check"


def doa_route(context: PolicyEvaluationContext) -> CheckResult:
    """The DOA tier this amount requires, and whether it clears the
    touchless ceiling.

    Always PASSes — like `payment_terms`, this check reports a routing fact
    (which tier, and whether a human is required at all) rather than judging
    the invoice; `sod_check` is where authority violations actually FAIL.
    """
    pack = context.policy
    amount = context.invoice.total_amount.value

    if amount.currency != pack.base_currency:
        return CheckResult(
            name=DOA_ROUTE,
            category=CheckCategory.AUTHORITY,
            verdict=Verdict.FLAG,
            severity=Severity.MEDIUM,
            reasoning=f"Amount is in {amount.currency}, not the tenant's base "
            f"currency {pack.base_currency}; DOA tiers are denominated in "
            f"{pack.base_currency} and cannot be compared directly.",
            threshold=f"amount in {pack.base_currency}",
            actual=amount.currency,
            forces_review=True,
        )

    tier = pack.doa.tier_for(amount.amount)
    touchless = pack.is_within_touchless(amount)

    reasoning = (
        f"{amount} requires {tier.tier!r}-tier authority"
        + (f" ({', '.join(tier.roles)})" if tier.roles else "")
        + ("; within the touchless ceiling." if touchless else "; a human approver is required.")
    )
    return CheckResult(
        name=DOA_ROUTE,
        category=CheckCategory.AUTHORITY,
        verdict=Verdict.PASS,
        reasoning=reasoning,
        threshold=f"touchless ceiling {pack.touchless_ceiling}",
        actual=f"tier={tier.tier}, touchless={touchless}",
        inputs={
            "tier": tier.tier,
            "roles": list(tier.roles),
            "touchless": touchless,
            "amount": str(amount.amount),
        },
    )


def sod_check(context: PolicyEvaluationContext) -> CheckResult:
    """Segregation of duties: the agent's authority is bounded, and known
    actor identities do not collide across coder/approver/payer roles.

    Two independent conditions, either of which alone is a violation:

    1. **Structural**: an amount above the touchless ceiling must never be
       auto-approved by the agent — enforced here as a direct evaluation of
       `SoDRules`, not merely inherited from the schema validator, so a
       violation is visible in the check ledger rather than only preventable
       at pack-authoring time.
    2. **Identity collision**: when coder/approver/payer identities are
       supplied on the context, no two of the roles the pack requires to
       differ may be held by the same person.

    Identity checks are `SKIP`, not `PASS`, when the relevant identities are
    not supplied — this engine slice runs ahead of the supervisor and
    approval-flow work (Slice 8/10) that will populate them, and a `PASS`
    here would misleadingly assert a control was exercised when it was not.
    """
    pack = context.policy

    violations: list[str] = []
    identity_checked = False

    # `SoDRules.agent_may_approve_above_touchless` is schema-enforced to
    # always be `False` (`policy_pack._forbid_unsafe_configuration`), so there
    # is no live branch here in which the pack itself grants the agent
    # above-touchless authority. This check re-asserts that invariant loudly
    # rather than silently trusting it, in case a pack ever reaches this
    # engine without having gone through `PolicyPack` validation.
    if pack.sod.agent_may_approve_above_touchless:
        raise PolicyPackInvariantError(
            "agent_may_approve_above_touchless is True on a pack reaching the "
            "engine. This must be schema-enforced false (FR-4.11) — refusing "
            "to evaluate sod_check against a pack that violates its own "
            "invariant."
        )

    if (
        pack.sod.approver_must_differ_from_coder
        and context.coder_identity is not None
        and context.approver_identity is not None
    ):
        identity_checked = True
        if context.coder_identity == context.approver_identity:
            violations.append(
                f"coder and approver are the same identity ({context.coder_identity!r})"
            )

    if (
        pack.sod.approver_must_differ_from_payer
        and context.approver_identity is not None
        and context.payer_identity is not None
    ):
        identity_checked = True
        if context.approver_identity == context.payer_identity:
            violations.append(
                f"approver and payer are the same identity ({context.approver_identity!r})"
            )

    if violations:
        reasoning = "Segregation of duties violated: " + "; ".join(violations) + "."
        return CheckResult(
            name=SOD_CHECK,
            category=CheckCategory.AUTHORITY,
            verdict=Verdict.FAIL,
            severity=Severity.CRITICAL,
            reasoning=reasoning,
            threshold="coder, approver, and payer identities distinct",
            actual="; ".join(violations),
            inputs={
                "coder_identity": context.coder_identity,
                "approver_identity": context.approver_identity,
                "payer_identity": context.payer_identity,
            },
            forces_review=True,
        )

    if not identity_checked:
        return CheckResult(
            name=SOD_CHECK,
            category=CheckCategory.AUTHORITY,
            verdict=Verdict.SKIP,
            reasoning="No actor identities supplied to check for role collision; "
            "the structural touchless-ceiling rule holds "
            "(agent_may_approve_above_touchless is false).",
            threshold="coder, approver, and payer identities distinct",
            actual="not supplied",
        )

    return CheckResult(
        name=SOD_CHECK,
        category=CheckCategory.AUTHORITY,
        verdict=Verdict.PASS,
        reasoning="Coder, approver, and payer identities do not collide, and "
        "the agent's authority remains bounded by the touchless ceiling.",
        threshold="coder, approver, and payer identities distinct",
        actual="distinct",
        inputs={
            "coder_identity": context.coder_identity,
            "approver_identity": context.approver_identity,
            "payer_identity": context.payer_identity,
        },
    )
