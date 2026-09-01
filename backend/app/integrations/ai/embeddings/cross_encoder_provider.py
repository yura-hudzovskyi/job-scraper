"""Local cross-encoder reranker — no per-request API cost, same "load once at
construction, run on a thread" shape as sentence_transformer_provider.py.
"""

import asyncio
import math

from sentence_transformers import CrossEncoder


class SentenceTransformersCrossEncoderProvider:
    def __init__(self, model_name: str):
        self._model = CrossEncoder(model_name)

    async def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        raw_scores = await asyncio.to_thread(self._model.predict, pairs)
        # Manual sigmoid rather than CrossEncoder's own activation_fn kwarg — cross-
        # encoder models here are trained with a single-logit regression head, and a
        # plain sigmoid maps that logit to a 0-1 relevance score without depending on
        # a specific sentence-transformers version's predict() signature.
        return [1.0 / (1.0 + math.exp(-float(score))) for score in raw_scores]
