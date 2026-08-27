"""Embedding provider abstraction, used for semantic similarity scoring
(see docs/matching-engine.md, Stage 3)."""

from typing import Protocol


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
