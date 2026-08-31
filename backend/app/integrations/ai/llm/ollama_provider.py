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


class OllamaModelNotFound(RuntimeError):
    """Ollama returns a bare 404 for both "no route" and "model not pulled" —
    this app only ever hits documented routes, so a 404 here always means the
    latter. Raised with the model name and a fix-it command so this doesn't read
    as a generic HTTP error buried in a traceback (see structured_completion)."""

    def __init__(self, model: str):
        super().__init__(
            f"Ollama model '{model}' is not available on this server — pull it first: "
            f"docker compose exec ollama ollama pull {model}"
        )
# Ollama silently truncates the prompt to whatever context window it uses —
# 2048-4096 tokens depending on version/model — unless a request explicitly asks
# for more. Job descriptions (and CV text, for the Ollama fallback in CV
# analysis) routinely run past that: a real, wordy senior JD easily approaches or
# exceeds it once the prompt wrapper and JSON-schema format constraint are added
# on top, silently truncating the posting's back half (Required/Desired
# Qualifications, most of the skill-dense text) before the model ever sees it —
# not the same failure as the model simply choosing to extract nothing. This is
# only the fallback used when a caller builds a provider directly without going
# through Settings — every real call site (see
# app/integrations/ai/llm/factory.py) passes Settings.ollama_num_ctx instead.
DEFAULT_NUM_CTX = 16384


class OllamaLLMProvider:
    def __init__(self, base_url: str, model: str = DEFAULT_MODEL, num_ctx: int = DEFAULT_NUM_CTX):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx
        # 14B-class CPU inference (the recommended default on the 24GB Oracle VM,
        # see docs/deployment.md) routinely takes well past 60-90s per structured
        # response — the old 120s timeout was sized for the 3B default and left
        # almost no margin under real load.
        self._client = httpx.AsyncClient(timeout=240.0)

    @property
    def model(self) -> str:
        return self._model

    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "format": schema.model_json_schema(),
                "stream": False,
                "options": {"num_ctx": self._num_ctx},
            },
        )
        if response.status_code == 404:
            raise OllamaModelNotFound(self._model)
        response.raise_for_status()
        content = response.json()["message"]["content"]
        data = schema.model_validate_json(content)
        return LLMResult(data=data, model_label=f"Ollama ({self._model})")
