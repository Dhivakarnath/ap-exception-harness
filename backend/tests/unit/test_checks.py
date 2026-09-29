"""CheckResult / CheckLedger semantics.

These types are the contract between the policy engine, the audit trail, the
stream, and the evals. Their invariants are therefore load-bearing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ap_agent.core.checks import (
    CheckCategory,
    CheckLedger,
    CheckResult,
    Severity,
    Verdict,
)


def _result(
    name: str = "some_rule",
    verdict: Verdict = Verdict.PASS,
    *,
    category: CheckCategory = CheckCategory.MATCHING,
    severity: Severity = Severity.INFO,
    forces_review: bool = False,
    reasoning: str = "because",
    **kwargs: object,
) -> CheckResult:
    return CheckResult(
        name=name,
        category=category,
        verdict=verdict,
        severity=severity,
        reasoning=reasoning,
        forces_review=forces_review,
        **kwargs,  # type: ignore[arg-type]
    )


class TestCoherenceInvariants:
    def test_pass_cannot_force_review(self) -> None:
        # A passing check that secretly forces review would make the ledger lie.
        with pytest.raises(ValidationError, match="Use FLAG"):
            _result(verdict=Verdict.PASS, forces_review=True)

    def test_critical_severity_cannot_pass(self) -> None:
        with pytest.raises(ValidationError, match="CRITICAL severity"):
            _result(verdict=Verdict.PASS, severity=Severity.CRITICAL)

    def test_flag_may_force_review(self) -> None:
        r = _result(verdict=Verdict.FLAG, forces_review=True, severity=Severity.HIGH)
        assert r.forces_review is True

    def test_reasoning_is_required(self) -> None:
        with pytest.raises(ValidationError):
            _result(reasoning="")

    def test_result_is_immutable(self) -> None:
        r = _result()
        with pytest.raises(ValidationError):
            r.verdict = Verdict.FAIL  # type: ignore[misc]


class TestBlockingSemantics:
    def test_fail_blocks(self) -> None:
        assert _result(verdict=Verdict.FAIL).blocks_auto_approval is True

    def test_flag_alone_does_not_block(self) -> None:
        # A flag raises attention; routing policy decides whether that means
        # review. A flag that must block sets forces_review explicitly.
        assert _result(verdict=Verdict.FLAG).blocks_auto_approval is False

    def test_flag_with_forces_review_blocks(self) -> None:
        r = _result(verdict=Verdict.FLAG, forces_review=True)
        assert r.blocks_auto_approval is True

    def test_pass_and_skip_do_not_block(self) -> None:
        assert _result(verdict=Verdict.PASS).blocks_auto_approval is False
        assert _result(verdict=Verdict.SKIP).blocks_auto_approval is False


class TestSkipIsRecordedNotOmitted:
    def test_skip_is_a_first_class_verdict(self) -> None:
        # Three-way match on a non-PO invoice must read as "did not apply",
        # distinguishable from "passed".
        r = _result(
            name="three_way_match",
            verdict=Verdict.SKIP,
            reasoning="No PO reference; three-way match not applicable.",
        )
        ledger = CheckLedger().append(r)
        assert ledger.by_name("three_way_match") is not None
        assert ledger.by_name("three_way_match").verdict is Verdict.SKIP  # type: ignore[union-attr]
        assert ledger.is_clean is True


class TestLedger:
    def test_append_is_immutable_and_ordered(self) -> None:
        a = _result(name="a")
        b = _result(name="b")
        first = CheckLedger().append(a)
        second = first.append(b)

        assert len(first) == 1
        assert len(second) == 2
        assert [r.name for r in second.results] == ["a", "b"]

    def test_extend(self) -> None:
        ledger = CheckLedger().extend([_result(name="a"), _result(name="b")])
        assert len(ledger) == 2

    def test_is_clean_when_all_pass(self) -> None:
        ledger = CheckLedger().extend([_result(name="a"), _result(name="b")])
        assert ledger.is_clean is True

    def test_is_not_clean_with_failure(self) -> None:
        ledger = CheckLedger().extend([_result(name="a"), _result(name="b", verdict=Verdict.FAIL)])
        assert ledger.is_clean is False
        assert len(ledger.failures) == 1

    def test_is_not_clean_with_review_forcing_flag(self) -> None:
        ledger = CheckLedger().append(
            _result(name="bank_change", verdict=Verdict.FLAG, forces_review=True)
        )
        assert ledger.is_clean is False
        assert len(ledger.review_forcing) == 1

    def test_critical_selection(self) -> None:
        ledger = CheckLedger().extend(
            [
                _result(name="ok"),
                _result(
                    name="bank_detail_change",
                    verdict=Verdict.FLAG,
                    severity=Severity.CRITICAL,
                    forces_review=True,
                ),
            ]
        )
        assert [r.name for r in ledger.critical] == ["bank_detail_change"]

    def test_by_name_missing_returns_none(self) -> None:
        assert CheckLedger().by_name("nope") is None

    def test_iter_results(self) -> None:
        ledger = CheckLedger().extend([_result(name="a"), _result(name="b")])
        assert [r.name for r in ledger.iter_results()] == ["a", "b"]


class TestSerialisationSurfaces:
    def test_stream_payload_carries_threshold_and_actual(self) -> None:
        r = _result(
            name="three_way_match",
            verdict=Verdict.FAIL,
            severity=Severity.HIGH,
            reasoning="price variance 4.2% exceeded 2.0% tolerance",
            threshold="2.0%",
            actual="4.2%",
            inputs={"po_number": "PO-1"},
            duration_ms=1.2,
        )
        payload = r.as_stream_payload()

        assert payload["name"] == "three_way_match"
        assert payload["verdict"] == "fail"
        assert payload["threshold"] == "2.0%"
        assert payload["actual"] == "4.2%"
        assert payload["inputs"] == {"po_number": "PO-1"}
        assert "at" in payload

    def test_span_attributes_are_flat_scalars(self) -> None:
        r = _result(threshold="2.0%", actual="1.8%", duration_ms=0.5)
        attrs = r.as_span_attributes()
        assert all(isinstance(v, (str, float, bool, int)) for v in attrs.values())
        assert attrs["check.name"] == "some_rule"
        assert attrs["check.threshold"] == "2.0%"

    def test_span_attributes_omit_absent_optionals(self) -> None:
        attrs = _result().as_span_attributes()
        assert "check.threshold" not in attrs
        assert "check.actual" not in attrs

    def test_citations_are_carried(self) -> None:
        r = _result(
            name="gl_coding",
            category=CheckCategory.CODING,
            citations=("policy.md#4.2", "precedent:INV-900"),
        )
        assert r.as_stream_payload()["citations"] == ["policy.md#4.2", "precedent:INV-900"]


class TestRendering:
    def test_render_line_uses_verdict_glyph(self) -> None:
        assert _result(verdict=Verdict.PASS).render_line().startswith("\u2713")
        assert _result(verdict=Verdict.FLAG).render_line().startswith("\u26a0")
        assert _result(verdict=Verdict.FAIL).render_line().startswith("\u2717")
        assert _result(verdict=Verdict.SKIP).render_line().startswith("\u2014")

    def test_render_ledger_is_one_line_per_result(self) -> None:
        ledger = CheckLedger().extend([_result(name="a"), _result(name="b")])
        assert len(ledger.render().splitlines()) == 2
