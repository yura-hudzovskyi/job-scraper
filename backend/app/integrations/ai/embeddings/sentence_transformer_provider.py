"""Default local embedding provider — no per-request API cost.

Loads the model once at construction (blocking — expected to happen at process
startup, not per-request) and runs the actual (CPU-bound) encode call in a thread so
it doesn't block the event loop.
"""

import asyncio

from sentence_transformers import SentenceTransformer


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str):
        self._model = SentenceTransformer(model_name)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = await asyncio.to_thread(self._model.encode, texts)
        return [vector.tolist() for vector in embeddings]
