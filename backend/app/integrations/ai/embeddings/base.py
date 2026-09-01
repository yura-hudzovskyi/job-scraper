"""Embedding provider abstraction, used for semantic similarity scoring
(see docs/matching-engine.md, Stage 3)."""

from typing import Protocol


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class CrossEncoderProvider(Protocol):
    """Jointly scores (text_a, text_b) pairs for relevance — unlike EmbeddingProvider,
    which encodes each text independently for later cosine comparison. Used to sharpen
    SemanticScorer's semantic_fit (see scoring.py)."""

    async def score(self, pairs: list[tuple[str, str]]) -> list[float]: ...
