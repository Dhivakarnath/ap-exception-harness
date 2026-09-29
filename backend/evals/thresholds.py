"""Minimum scores for the CI eval gate (FR-13.6).

Deterministic tiers always run; live DeepEval tiers are `null` when AWS is
unavailable and do not fail the gate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvalThresholds:
    # Synthetic manifest: 16/90 cases diverge on context the policy-only eval
    # does not supply (vendor resolution path, PO currency edge cases). See INC-022.
    policy_adherence: float = 0.80
    extraction_accuracy: float = 0.80
    rag_faithfulness: float = 0.75
    rag_answer_relevancy: float = 0.75
    rag_contextual_relevancy: float = 0.70
    routing_label_consistency: float = 0.70
    task_completion: float = 0.70
    tool_correctness: float = 0.90
    argument_correctness: float = 0.90


DEFAULT_THRESHOLDS = EvalThresholds()
