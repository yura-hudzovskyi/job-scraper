"""Optional hosted embedding provider, used only if configured."""


class OpenAIEmbeddingProvider:
    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
