"""Backward-compatible import for the production Bedrock DeepEval judge."""

from ap_agent.evaluation.judge import BedrockConverseJudge, bedrock_judge_model

__all__ = ["BedrockConverseJudge", "bedrock_judge_model"]
