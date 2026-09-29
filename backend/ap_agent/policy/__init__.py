"""The deterministic policy engine — the project's core differentiator.

Every rule in this package is a **pure function**: `(context) -> CheckResult`,
built from a `PolicyEvaluationContext` (an already-resolved `Invoice` plus its
`PolicyPack` and whatever counterpart records apply — a `PurchaseOrder`, a
`GoodsReceipt`, a resolved `Vendor`, historical bills for duplicate detection).
No function in this package calls a model, makes a network request, or reads
a clock outside what is passed to it (FR-7.4, verified by
`test_no_rule_evaluator_invokes_a_model`).

This is deliberate, not incidental. Money decisions that are exact — does the
math reconcile, does the quantity match the goods receipt, has this vendor been
paid this exact bill before — must never be probabilistic. A model is
excellent at reading a blurry scan; it has no business deciding whether
`120 * 12.00 == 1440.00`. Putting that decision in a prompt would mean an
identical invoice could get two different verdicts on two different days,
which is not a property an audit trail can survive.

**Design commitments carried from the rest of the project:**

* **Fail loud, never fall back.** A rule that cannot be evaluated (missing PO
  when one is required, unsupported currency) raises via `ap_agent.errors`,
  the same fail-loud discipline as extraction and parsing. There is no
  "assume PASS" branch anywhere in this package.
* **Four verdicts, not two.** `PASS` / `FLAG` / `FAIL` / `SKIP`, per
  `core.checks.Verdict`. `SKIP` matters as much as `FAIL`: a non-PO invoice's
  `three_way_match` is not "passed", it never ran, and a reviewer must be able
  to tell the difference.
* **Threshold vs actual, always.** Every `CheckResult` states what the rule
  compared against what it found, not just a verdict — "price variance 4.2%
  exceeded the 2.0% tolerance" is auditable; "failed" is not.
* **Config drives behaviour, not code.** The same functions run for every
  tenant; `PolicyPack` is what makes manufacturing's zero-tolerance,
  GRN-mandatory posture and retail's non-PO-heavy, looser-tolerance posture
  two configuration files rather than two codebases (ADR-004).

**What this package does not do.** It does not fetch a PO, GRN, or vendor from
an ERP, and it does not resolve a vendor name to a vendor ID — those are the
connector and mapping layers' jobs (built separately). This package receives
already-loaded canonical objects and evaluates rules against them. It also
does not decide the final `Route` or write a `Decision` — that is the
supervisor's job, informed by the `CheckLedger` this package produces.
"""

from ap_agent.policy.context import PolicyEvaluationContext
from ap_agent.policy.engine import CHECK_NAMES, evaluate_all

__all__ = ["CHECK_NAMES", "PolicyEvaluationContext", "evaluate_all"]
