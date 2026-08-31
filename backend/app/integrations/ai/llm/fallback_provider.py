"""Wraps a primary LLMProvider with a fallback, retried only for exceptions the
caller explicitly marks retryable (e.g. a specific vendor's rate-limit error) — a
real auth/config error from the primary should surface loudly, not be silently
masked by a fallback that hides a broken API key. Provider-agnostic: takes the
exception classes and the predicate as constructor arguments rather than importing
any specific vendor SDK, so it's reusable and testable with fakes.

An optional RetryCircuitBreaker adds a second layer on top of plain try-then-
fallback: once a retryable failure is actually observed (e.g. Gemini's daily
free-tier quota exhausted), further calls skip the primary entirely for a cooldown
period instead of paying for — and waiting on — a network round trip that's
guaranteed to fail again until that quota resets. Without one, every single call
pays that cost, every time, for as long as the primary stays exhausted (see
circuit_breaker.py for the concrete Gemini-specific implementation). The
breaker is itself just a Protocol here, so this class stays dependency-free and
testable with fakes exactly like before.
"""

from collections.abc import Callable
from typing import Protocol

from app.integrations.ai.llm.base import LLMProvider, LLMResult, T


class RetryCircuitBreaker(Protocol):
    async def is_open(self) -> bool:
        """True if the primary should be skipped entirely right now."""
        ...

    async def record_failure(self) -> None:
        """Called immediately after a retryable failure — starts (or extends)
        whatever cooldown keeps is_open() returning True."""
        ...


class FallbackLLMProvider:
    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        is_retryable: Callable[[Exception], bool],
        circuit_breaker: RetryCircuitBreaker | None = None,
    ):
        self._primary = primary
        self._fallback = fallback
        self._is_retryable = is_retryable
        self._circuit_breaker = circuit_breaker

    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        if self._circuit_breaker is not None and await self._circuit_breaker.is_open():
            return await self._fallback.structured_completion(prompt, schema)

        try:
            return await self._primary.structured_completion(prompt, schema)
        except Exception as exc:
            if not self._is_retryable(exc):
                raise
            if self._circuit_breaker is not None:
                await self._circuit_breaker.record_failure()
            return await self._fallback.structured_completion(prompt, schema)
