"""Bedrock model access.

Nova Lite via `ChatBedrockConverse` (ADR-001). Verified working for both required
behaviours before this module was written:

* `with_structured_output(PydanticModel)` returns a validated instance;
* image blocks are accepted and read — a degraded PNG fixture yielded the correct
  invoice number.

`temperature=0` throughout. Extraction is a reading task with one correct answer;
sampling variety would only add non-determinism to a step whose output is compared
against ground truth.

The `ModelClient` protocol exists so the extractor can be unit-tested against a
scripted stub. Without it, every extraction test would need live credentials, which
would make the fast test tier depend on the network and on someone's AWS account.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Protocol

from ap_agent.config import get_settings
from ap_agent.errors import (
    CredentialsError,
    ErrorClass,
    ErrorContext,
    TransientError,
    classify_exception,
    is_credentials_error,
)

logger = logging.getLogger(__name__)


class ModelClient(Protocol):
    """Minimal surface the extractor needs.

    Narrow on purpose: the extractor should not be able to reach for chat history,
    tools, or streaming, because none of those belong in a single-shot reading task.
    """

    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: list[tuple[str, bytes]],
        schema: type,
    ) -> tuple[Any, dict[str, int]]:
        """Return (validated instance, token usage)."""
        ...


class BedrockModelClient:
    """Live Nova Lite client."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        region: str | None = None,
        max_tokens: int = 4096,
        repair_attempts: int = 3,
    ) -> None:
        settings = get_settings()
        self._model_id = model_id or settings.bedrock_model_id
        self._region = region or settings.aws_region
        self._max_tokens = max_tokens
        # Bounded. Unbounded re-asking would turn an unreliable model into an
        # unbounded bill, and a schema the model cannot satisfy is a design problem
        # to surface, not to grind against.
        self._repair_attempts = max(1, repair_attempts)
        self._llm: Any = None

    @property
    def model_id(self) -> str:
        return self._model_id

    def _get_llm(self) -> Any:
        if self._llm is None:
            from langchain_aws import ChatBedrockConverse

            # `model_id` is the declared field; `model` works as a populate-by-name
            # alias but is not the typed parameter.
            self._llm = ChatBedrockConverse(
                model_id=self._model_id,
                region_name=self._region,
                # Deterministic: extraction has one correct answer.
                temperature=0,
                max_tokens=self._max_tokens,
            )
        return self._llm

    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: list[tuple[str, bytes]],
        schema: type,
    ) -> tuple[Any, dict[str, int]]:
        llm = self._get_llm()
        structured = llm.with_structured_output(schema, include_raw=True)

        # Images FIRST, then text. Measured: with the text block first, Nova Lite
        # anchored on the parsed text and ignored a perfectly legible page image,
        # missing a tax line whose label the text pipeline had dropped. Leading with
        # the image makes it the primary evidence rather than an afterthought.
        # See INC-004.
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source_type": "base64",
                "mime_type": mime_type,
                "data": base64.b64encode(data).decode("ascii"),
            }
            for mime_type, data in images
        ]
        content.append({"type": "text", "text": user_prompt})

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        cumulative: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        last_error: Exception | None = None
        # Attempts used are a confidence signal in their own right (see
        # `extract.confidence`): an invoice the model got right on the first
        # try is a different quality of reading than one that needed correcting.

        # Bounded structured-output repair. A schema-validation failure is
        # MODEL_RECOVERABLE: the model produced malformed output and can plausibly
        # correct it when told what was wrong. Re-asking is categorically different
        # from coercing — we are not inventing a value, we are declining to accept
        # an invalid response and requesting a valid one.
        #
        # Measured need: Nova Lite intermittently omits a required field on a schema
        # with this many nested objects. Observed twice on the fixture set — once
        # dropping `currency`, once echoing a totals row as a line. See INC-004.
        from ap_agent.llm.invoke_context import langchain_invoke_config

        invoke_config = langchain_invoke_config()
        for attempt in range(1, self._repair_attempts + 1):
            try:
                result = (
                    structured.invoke(messages, config=invoke_config)
                    if invoke_config
                    else structured.invoke(messages)
                )
            except Exception as exc:
                # An authentication failure is not a reading failure. Relabel it
                # so the message names the real fix (refresh the credential)
                # instead of implying the model or the invoice was at fault.
                # See INC-019.
                if is_credentials_error(exc):
                    raise CredentialsError(
                        f"AWS credentials were rejected calling Bedrock "
                        f"({self._model_id}): the security token is expired or "
                        "invalid. Refresh your AWS login (e.g. `aws sso login`) "
                        "and retry — the document was not read, and nothing was "
                        "extracted.",
                        context=ErrorContext(stage="extract.bedrock"),
                        cause=exc,
                    ) from exc
                # Transport-level failure. Only genuinely transient errors are
                # marked retryable; the classification lives here so the retry
                # policy stays in one place (FR-8.1).
                if classify_exception(exc) is ErrorClass.TRANSIENT:
                    raise TransientError(
                        f"Bedrock call to {self._model_id} failed transiently.",
                        context=ErrorContext(stage="extract.bedrock"),
                        cause=exc,
                    ) from exc
                raise

            try:
                parsed, usage = _unpack(result)
            except SchemaValidationFailure as exc:
                last_error = exc
                _accumulate(cumulative, exc.usage)
                if attempt == self._repair_attempts:
                    break
                logger.warning(
                    "Structured output failed validation (attempt %d/%d): %s",
                    attempt,
                    self._repair_attempts,
                    exc.detail,
                )
                messages = [*messages, *_repair_turn(exc.detail)]
                continue

            _accumulate(cumulative, usage)
            cumulative["attempts_used"] = attempt
            return parsed, cumulative

        # Exhausted. Fatal: we never fabricate a reading, and repeated malformed
        # output is a real problem the caller must surface rather than absorb.
        raise ValueError(
            f"Model output failed schema validation after {self._repair_attempts} "
            f"attempts. Last error: {getattr(last_error, 'detail', last_error)}"
        )


class SchemaValidationFailure(Exception):
    """The model returned output that does not satisfy the schema.

    Carries the validation detail so it can be fed back to the model, and the token
    usage so a failed attempt still costs what it cost.
    """

    def __init__(self, detail: str, usage: dict[str, int]) -> None:
        super().__init__(detail)
        self.detail = detail
        self.usage = usage


def _repair_turn(detail: str) -> list[dict[str, Any]]:
    """The corrective exchange appended before re-asking.

    States the specific validation error. A bare "try again" would be a coin flip;
    naming the offending field is what makes the second attempt likely to succeed.
    """
    return [
        {
            "role": "assistant",
            "content": "(previous response omitted — it did not satisfy the schema)",
        },
        {
            "role": "user",
            "content": (
                "Your previous response failed schema validation:\n\n"
                f"{detail}\n\n"
                "Return the complete object again, including every required field. "
                "Do not omit a field because it was hard to read: report it with an "
                "empty value and low confidence instead. Do not add fields that are "
                "not in the schema."
            ),
        },
    ]


def _accumulate(total: dict[str, int], usage: dict[str, int]) -> None:
    """Add one call's usage into the running total.

    Failed attempts are counted: a repaired extraction genuinely cost two calls, and
    cost reporting that hid that would understate the price of unreliability.
    """
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key, 0) or 0)


def _unpack(result: Any) -> tuple[Any, dict[str, int]]:
    """Split `include_raw=True` output into the parsed value and token usage.

    `include_raw=True` is used so token counts are available for cost attribution
    (FR-12.5). Without it the usage metadata is discarded and a run's cost cannot be
    reported.
    """
    if isinstance(result, dict):
        parsed = result.get("parsed")
        raw = result.get("raw")
        parsing_error = result.get("parsing_error")
        usage = _usage_from(raw)

        if parsed is None:
            raise SchemaValidationFailure(
                str(parsing_error or "model returned no parsed object"), usage
            )
        return parsed, usage

    return result, {}


def _usage_from(raw: Any) -> dict[str, int]:
    metadata = getattr(raw, "usage_metadata", None) or {}
    if not isinstance(metadata, dict):
        return {}
    return {
        "input_tokens": int(metadata.get("input_tokens", 0) or 0),
        "output_tokens": int(metadata.get("output_tokens", 0) or 0),
        "total_tokens": int(metadata.get("total_tokens", 0) or 0),
    }


class ScriptedModelClient:
    """Test double returning pre-built responses.

    Lets extraction logic — the mapping, typing, confidence, and failure paths — be
    tested exhaustively in the fast tier without network access or credentials.
    """

    def __init__(
        self,
        responses: list[Any] | None = None,
        *,
        usage: dict[str, int] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._usage = usage or {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: list[tuple[str, bytes]],
        schema: type,
    ) -> tuple[Any, dict[str, int]]:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "image_count": len(images),
                "image_mime_types": [m for m, _ in images],
                "schema": schema.__name__,
            }
        )
        if self._raises is not None:
            raise self._raises
        if not self._responses:
            raise AssertionError("ScriptedModelClient exhausted: no response queued.")
        return self._responses.pop(0), self._usage
