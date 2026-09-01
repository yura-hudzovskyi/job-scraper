"""Primary hosted LLM provider for the "quality matters" call site (CV analysis) —
Google's free tier makes this the default paid-model-quality option that costs
nothing, unlike OpenAI/Anthropic which need a funded account. See factory.py for the
fallback-to-Ollama wiring on rate limit.

Uses the official `google-genai` SDK (the current unified SDK — the older
`google-generativeai` package is deprecated, don't use it). Structured output via
response_schema/response_mime_type, verified against google-genai 2.20.0: a Pydantic
schema class passed directly as response_schema is supported, and the response's
raw JSON text is on `.text`.
"""

from google import genai
from google.genai import types

from app.integrations.ai.llm.base import LLMResult, T


class GeminiLLMProvider:
    def __init__(self, api_key: str, model: str):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        if response.text is None:
            raise ValueError(f"Gemini response had no text to parse into {schema.__name__}")
        data = schema.model_validate_json(response.text)
        return LLMResult(data=data, model_label=f"Gemini ({self._model})")
