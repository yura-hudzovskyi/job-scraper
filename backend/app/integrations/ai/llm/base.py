"""LLM provider abstraction. Domain/service code must depend on this, never on a
specific vendor SDK. See docs/matching-engine.md — LLMs never own deterministic data,
they're used for extraction, reasoning, reranking, summarization and cover letters.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    async def structured_completion(self, prompt: str, schema: type[T]) -> T:
        """Return a completion validated against the given Pydantic schema."""
        ...
