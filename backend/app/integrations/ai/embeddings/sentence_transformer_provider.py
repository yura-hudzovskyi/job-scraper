"""Default local embedding provider — no per-request API cost."""


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str):
        self._model_name = model_name

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
