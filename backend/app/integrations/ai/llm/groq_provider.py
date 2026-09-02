"""Job-pipeline LLM provider — Groq's free tier runs open models (Llama 3.x, etc.)
on dedicated inference hardware, so a response comes back in a second or two —
fast enough to churn through a real backlog of scraped jobs. See
docs/matching-engine.md's "LLM provider policy" for where this fits.

Uses the `openai` SDK pointed at Groq's OpenAI-compatible endpoint — Groq's own
documented integration path, and `openai` is already a dependency here (see
openai_provider.py), so this needs no new package. Deliberately uses plain JSON
mode (`response_format={"type": "json_object"}`) plus manual Pydantic validation,
not the SDK's `.parse()` schema-enforcement helper OpenAILLMProvider uses: that
helper assumes OpenAI's specific structured-outputs wire contract
(`response_format: {"type": "json_schema", ...}`), and whether every model in
Groq's catalog implements that exact contract wasn't verified against a live
account in this environment. JSON mode (guaranteed-valid-JSON, not
guaranteed-matching-schema) is the one thing documented across Groq's whole
catalog, so the schema is spelled out in the prompt instead and validated here.

max_retries=0: the `openai` SDK retries 429s internally by default (with its own
backoff) before ever raising back to caller code — which fights FallbackLLMProvider
and GeminiCircuitBreaker/FixedCooldownCircuitBreaker's whole reason for existing.
Every retryable failure needs to surface immediately so the fallback (and the
circuit breaker recording it) can take over right away, not after the SDK has
already silently burned a few seconds and a couple of real HTTP requests against
a quota that's already exhausted for this window.
"""

import openai

from app.integrations.ai.llm.base import LLMResult, T

_BASE_URL = "https://api.groq.com/openai/v1"


class GroqLLMProvider:
    def __init__(self, api_key: str, model: str):
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=_BASE_URL, max_retries=0)
        self._model = model

    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Respond with a single JSON object and nothing else, matching "
                        f"exactly this JSON schema: {schema.model_json_schema()}"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError(f"Groq response had no content to parse into {schema.__name__}")
        data = schema.model_validate_json(content)
        return LLMResult(data=data, model_label=f"Groq ({self._model})")
