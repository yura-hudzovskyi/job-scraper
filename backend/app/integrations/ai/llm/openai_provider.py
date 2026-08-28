"""Optional hosted LLM provider, used only if configured. See docs/matching-engine.md.

Uses the official `openai` SDK's chat.completions.parse(), which validates the
response against the given Pydantic schema server-side (response_format) and returns
it on choices[0].message.parsed.
"""

import openai

from app.integrations.ai.llm.base import T

DEFAULT_MODEL = "gpt-4o"


class OpenAILLMProvider:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model

    async def structured_completion(self, prompt: str, schema: type[T]) -> T:
        response = await self._client.chat.completions.parse(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format=schema,
        )
        message = response.choices[0].message
        if message.parsed is None:
            raise ValueError(
                f"OpenAI response did not parse into {schema.__name__} "
                f"(refusal={message.refusal!r})"
            )
        return message.parsed
