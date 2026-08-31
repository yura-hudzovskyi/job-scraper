"""Reactive companion to DailyCallBudget (budget.py). That one is a proactive cap
this app enforces on itself, checked *before* every call. This is a reactive
breaker that remembers when the *provider's own* quota already said no, so
FallbackLLMProvider doesn't pay for — and wait on — a network round trip that's
guaranteed to fail again: once Gemini returns 429 (see
app/integrations/ai/llm/factory.py's _is_gemini_rate_limited), every subsequent
call skips straight to Ollama for the rest of the cooldown instead of re-trying
Gemini and hitting the same wall on every single job scored.

Gemini's free-tier quota (see docs/matching-engine.md) is a *daily* cap
("GenerateRequestsPerDayPerProjectPerModel-FreeTier" per the API's own error
detail) — not a short per-minute backoff — so the cooldown here runs until the
next UTC midnight rather than trusting the API's own `retryDelay` hint, which in
practice has been observed to report a much shorter delay (tens of seconds) than
the daily quota it's attached to actually resets on. Retrying that soon would
just burn another wasted call on the same exhausted daily bucket.
"""

from datetime import UTC, datetime, timedelta

import redis.asyncio as redis

_KEY_PREFIX = "gemini_exhausted"


def _seconds_until_next_utc_midnight(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


class GeminiCircuitBreaker:
    """Keyed by model, since the quota itself is per-model — a cooldown on
    gemini-2.0-flash must not silently also block a different configured model."""

    def __init__(self, redis_client: redis.Redis, model: str):
        self._redis = redis_client
        self._key = f"{_KEY_PREFIX}:{model}"

    async def is_open(self) -> bool:
        """Fails open (False) on a Redis error — an optional cost-saving layer
        must never itself block a call the primary might still be able to serve."""
        try:
            return bool(await self._redis.exists(self._key))
        except redis.RedisError:
            return False

    async def record_failure(self) -> None:
        try:
            cooldown = _seconds_until_next_utc_midnight(datetime.now(UTC))
            await self._redis.set(self._key, "1", ex=cooldown)
        except redis.RedisError:
            pass
