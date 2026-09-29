"""DeepEval judge backed by the production Bedrock Converse client."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from deepeval.models import DeepEvalBaseLLM


class BedrockConverseJudge(DeepEvalBaseLLM):  # type: ignore[no-untyped-call]
    """Use the configured Bedrock model without storing AWS credentials."""

    def load_model(self, *args: object, **kwargs: object) -> BedrockConverseJudge:
        from langchain_aws import ChatBedrockConverse

        from ap_agent.config import get_settings

        settings = get_settings()
        self._client = ChatBedrockConverse(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
            temperature=0,
        )
        return self

    def generate(self, prompt: str, **kwargs: Any) -> str | Any:
        from langchain_core.messages import HumanMessage

        schema = kwargs.get("schema")
        if schema is not None:
            schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
            prompt = (
                f"{prompt}\n\nReturn one JSON object only. It must validate "
                f"against this JSON Schema:\n{schema_json}"
            )

        response = self._client.invoke([HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return str(content)

    async def a_generate(self, prompt: str, **kwargs: Any) -> str | Any:
        return await asyncio.to_thread(self.generate, prompt, **kwargs)

    def get_model_name(self, *args: object, **kwargs: object) -> str:
        return self.name or "bedrock-converse"


def bedrock_judge_model() -> BedrockConverseJudge:
    from ap_agent.config import get_settings

    settings = get_settings()
    return BedrockConverseJudge(model=settings.bedrock_model_id)
