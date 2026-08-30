"""Wraps a primary LLMProvider with a fallback, retried only for exceptions the
caller explicitly marks retryable (e.g. a specific vendor's rate-limit error) — a
real auth/config error from the primary should surface loudly, not be silently
masked by a fallback that hides a broken API key. Provider-agnostic: takes the
exception classes and the predicate as constructor arguments rather than importing
any specific vendor SDK, so it's reusable and testable with fakes.
"""

from collections.abc import Callable

from app.integrations.ai.llm.base import LLMProvider, LLMResult, T


class FallbackLLMProvider:
    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        is_retryable: Callable[[Exception], bool],
    ):
        self._primary = primary
        self._fallback = fallback
        self._is_retryable = is_retryable

    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        try:
            return await self._primary.structured_completion(prompt, schema)
        except Exception as exc:
            if not self._is_retryable(exc):
                raise
            return await self._fallback.structured_completion(prompt, schema)
