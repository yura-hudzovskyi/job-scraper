"""Optional hosted embedding provider, used only if configured."""

import openai

DEFAULT_MODEL = "text-embedding-3-small"


class OpenAIEmbeddingProvider:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in response.data]
