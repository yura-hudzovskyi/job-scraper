"""LLM provider abstraction. Domain/service code must depend on this, never on a
specific vendor SDK. See docs/matching-engine.md — LLMs never own deterministic data,
they're used for extraction, reasoning, reranking, summarization and cover letters.
"""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    """Wraps the validated completion with which model actually produced it, so
    callers can attribute AI-generated content in the UI — including after a
    FallbackLLMProvider silently swaps providers mid-request. A property on the
    provider instance can't do this safely (races under concurrent calls on the
    same instance); a value returned per-call can.
    """

    data: T
    model_label: str


class LLMProvider(Protocol):
    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        """Return a completion validated against the given Pydantic schema, tagged
        with which model produced it."""
        ...
