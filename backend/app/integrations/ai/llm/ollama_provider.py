"""Default local LLM provider — no per-request API cost. See docs/matching-engine.md."""

from app.integrations.ai.llm.base import T


class OllamaLLMProvider:
    def __init__(self, base_url: str, model: str):
        self._base_url = base_url
        self._model = model

    async def structured_completion(self, prompt: str, schema: type[T]) -> T:
        raise NotImplementedError
