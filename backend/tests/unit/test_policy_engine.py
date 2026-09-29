"""The orchestrator (`evaluate_all`) and the properties the whole engine must
hold regardless of which rule is being evaluated (FR-7.4).

Three things are asserted here that no single-rule test file can:

* every check name the orchestrator is supposed to run actually appears in
  the returned ledger, in the fixed, documented order;
* **no rule evaluator ever invokes a model** — the property that makes this
  package deterministic in the first place;
* **identical inputs always yield identical verdicts** — a property test
  over repeated evaluation, not a single assertion, since a single run
  passing proves nothing about a hidden source of non-determinism (a
  `datetime.now()` call, a dict-ordering dependency, a hash-seed-dependent
  iteration) that a lucky one-shot test would not surface.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ap_agent.core.checks import CheckLedger, Verdict
from ap_agent.policy import evaluate_all
from ap_agent.policy.engine import CHECK_NAMES
from ap_agent.policy.matching import THREE_WAY_MATCH
from tests.unit.policy_fixtures import context, goods_receipt, invoice, purchase_order

POLICY_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "ap_agent" / "policy"


class TestEvaluateAll:
    def test_returns_a_result_for_every_declared_check(self) -> None:
        ledger = evaluate_all(context())
        names = {r.name for r in ledger.iter_results()}
        assert names == set(CHECK_NAMES)

    def test_results_are_in_the_declared_order(self) -> None:
        ledger = evaluate_all(context())
        assert tuple(r.name for r in ledger.iter_results()) == CHECK_NAMES

    def test_clean_invoice_is_a_clean_ledger(self) -> None:
        po = purchase_order(po_number="PO-2001", quantity="120", unit_price="12.00")
        grn = goods_receipt(po_number="PO-2001", quantity="120", unit_price="12.00")
        inv = invoice(
            po_reference="PO-2001",
            subtotal="240.00",
            tax_amount="19.80",
            total_amount="259.80",
        )
        ledger = evaluate_all(context(inv=inv, po=po, grn=grn))
        assert ledger.is_clean is True

    def test_non_po_invoice_skips_three_way_match_without_blocking(self) -> None:
        inv = invoice(po_reference=None)
        ledger = evaluate_all(context(inv=inv))
        result = ledger.by_name(THREE_WAY_MATCH)
        assert result is not None
        assert result.verdict is Verdict.SKIP
        # SKIP does not block auto-approval on its own.
        assert result.blocks_auto_approval is False

    def test_a_single_failure_makes_the_ledger_not_clean(self) -> None:
        inv = invoice(po_reference="PO-9999")  # no matching PO supplied
        ledger = evaluate_all(context(inv=inv, po=None))
        assert ledger.is_clean is False
        assert ledger.by_name(THREE_WAY_MATCH) is not None
        assert ledger.by_name(THREE_WAY_MATCH).verdict is Verdict.FAIL  # type: ignore[union-attr]

    def test_returns_a_check_ledger_instance(self) -> None:
        assert isinstance(evaluate_all(context()), CheckLedger)


class TestDeterminism:
    """FR-7.4: identical inputs must always yield identical verdicts."""

    @pytest.mark.parametrize(
        "build_ctx",
        [
            lambda: context(),
            lambda: context(inv=invoice(po_reference=None)),
            lambda: context(
                inv=invoice(po_reference="PO-2001"),
                po=purchase_order(po_number="PO-2001", quantity="120", unit_price="12.00"),
                grn=goods_receipt(po_number="PO-2001", quantity="120", unit_price="12.00"),
            ),
        ],
    )
    def test_repeated_evaluation_of_identical_inputs_is_byte_identical(self, build_ctx) -> None:  # type: ignore[no-untyped-def]
        first = evaluate_all(build_ctx())
        for _ in range(25):
            again = evaluate_all(build_ctx())
            assert _ledger_signature(first) == _ledger_signature(again)

    def test_evaluation_order_does_not_depend_on_dict_iteration(self) -> None:
        # Run many times to surface any accidental dependence on set/dict
        # iteration order, which varies by hash seed across processes but not
        # within one — a real regression here would be intermittent, not
        # always-failing, which is exactly what repetition is for.
        results = [tuple(r.name for r in evaluate_all(context()).iter_results()) for _ in range(50)]
        assert len(set(results)) == 1


def _ledger_signature(ledger: CheckLedger) -> tuple[tuple[str, str, str, str | None, str | None], ...]:
    """A comparable projection of a ledger's decision-relevant content.

    Excludes `evaluated_at` and `duration_ms` deliberately — those are
    genuinely expected to differ between runs (wall-clock timestamps), and
    are not part of what "identical inputs yield identical verdicts" means.
    """
    return tuple(
        (r.name, r.verdict.value, r.reasoning, r.threshold, r.actual) for r in ledger.iter_results()
    )


class TestNoRuleEvaluatorInvokesAModel:
    """FR-7.4's other half: this package must contain no path to a model call.

    A behavioural test (mocking out every possible model client and asserting
    it was never called) would only catch a model call made through the
    clients this test happened to think of. A static check of the actual
    source text is the stronger guarantee here — grep for the vocabulary a
    model call would necessarily use, across every module in the package.
    """

    _FORBIDDEN_IMPORT_MODULES = (
        "langchain",
        "langgraph",
        "boto3",
        "botocore",
        "anthropic",
        "openai",
    )
    _FORBIDDEN_NAME_FRAGMENTS = ("bedrock", "llm", "chatmodel", "invoke_model")

    def _policy_modules(self) -> list[Path]:
        return sorted(POLICY_PACKAGE_DIR.glob("*.py"))

    def test_package_contains_at_least_the_expected_modules(self) -> None:
        # Guards the test itself against silently checking nothing if the
        # package layout ever changes.
        names = {p.stem for p in self._policy_modules()}
        assert {"engine", "matching", "arithmetic", "authority"} <= names

    def test_no_module_imports_a_model_related_package(self) -> None:
        for path in self._policy_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module = alias.name
                        self._assert_not_forbidden(module, path)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                    self._assert_not_forbidden(module, path)

    def _assert_not_forbidden(self, module: str, path: Path) -> None:
        top_level = module.split(".")[0].casefold()
        assert top_level not in self._FORBIDDEN_IMPORT_MODULES, (
            f"{path.name} imports {module!r}, a model-related package. Rule "
            "evaluators must be pure functions with no path to a model call."
        )

    def test_no_module_source_mentions_model_call_vocabulary(self) -> None:
        for path in self._policy_modules():
            source = path.read_text(encoding="utf-8").casefold()
            for fragment in self._FORBIDDEN_NAME_FRAGMENTS:
                assert fragment not in source, (
                    f"{path.name} contains {fragment!r}, suggesting a model "
                    "call may have been introduced into the deterministic "
                    "policy engine."
                )

    def test_every_public_check_function_has_no_async_def(self) -> None:
        # A model call is I/O-bound and this project's model client is
        # invoked synchronously today, but `async def` on a rule evaluator
        # would itself be a signal that a network call crept in — pure
        # arithmetic and comparisons have no reason to be async.
        for path in self._policy_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                assert not isinstance(node, ast.AsyncFunctionDef), (
                    f"{path.name} defines an async function "
                    f"({node.name!r}); no rule evaluator should need to be "
                    "async."
                )
