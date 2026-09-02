"""Rerank engine abstraction — see docs/ai-pipeline-v3.md (D).

A reranker answers a different question from an embedding model: not "where does
this document sit in space" but "how well does *this* document answer *this*
query", with both texts read together. That is more accurate and more expensive,
which is why it runs over the retrieved top-K rather than the corpus.

`model_id` is part of the contract because raw relevance scores are
model-specific: they are only comparable after a model-specific calibration, and
a stored result has to say which model produced it.
"""

from typing import Protocol


class RerankEngine(Protocol):
    @property
    def model_id(self) -> str:
        """Stable identifier, e.g. "voyage:rerank-3" — recorded in provenance and
        used to pick the right calibration."""
        ...

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Raw relevance for each document, in the order they were given. Raising
        is how an engine says "I couldn't do this set" — the caller then discards
        whatever came back and reruns the whole set on the next engine, because
        half a ranking from one model and half from another is not a ranking."""
        ...
