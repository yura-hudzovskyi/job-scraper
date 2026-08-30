"""Default local LLM provider — no per-request API cost. See docs/matching-engine.md.

Ollama's /api/chat accepts a JSON schema via `format` and constrains output to it —
no tool-use plumbing needed. Uses httpx directly rather than a vendor SDK: it's a
simple local JSON API and Ollama has no official Python client to standardize on.
No local Ollama server was available to verify this live in this environment — the
request shape follows Ollama's documented structured-output API; verify against a
real `ollama serve` before relying on it.
"""

import httpx

from app.integrations.ai.llm.base import LLMResult, T

DEFAULT_MODEL = "llama3.1"


class OllamaLLMProvider:
    def __init__(self, base_url: str, model: str = DEFAULT_MODEL):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=120.0)

    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "format": schema.model_json_schema(),
                "stream": False,
            },
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        data = schema.model_validate_json(content)
        return LLMResult(data=data, model_label=f"Ollama ({self._model})")
