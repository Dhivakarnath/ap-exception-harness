"""Shared LLM plumbing used by both the extractor and the GL coder.

Kept in one place so the two model call sites cannot drift: token-usage capture
and cost are things you get subtly wrong independently and never notice until a
number is off. One helper, one definition.
"""
