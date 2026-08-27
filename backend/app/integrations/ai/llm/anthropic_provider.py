"""Optional hosted LLM provider, used only if configured. See docs/matching-engine.md."""

from app.integrations.ai.llm.base import T


class AnthropicLLMProvider:
    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self._model = model

    async def structured_completion(self, prompt: str, schema: type[T]) -> T:
        raise NotImplementedError
