"""Optional hosted LLM provider, used only if configured. See docs/matching-engine.md.

Uses the official `anthropic` SDK's messages.parse(), which validates the response
against the given Pydantic schema server-side and returns it on .parsed_output —
no manual JSON parsing or tool-use plumbing needed.
"""

import anthropic

from app.integrations.ai.llm.base import LLMResult, T


class AnthropicLLMProvider:
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        response = await self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        if response.parsed_output is None:
            raise ValueError(
                f"Anthropic response did not parse into {schema.__name__} "
                f"(stop_reason={response.stop_reason!r})"
            )
        return LLMResult(data=response.parsed_output, model_label=f"Anthropic ({self._model})")
