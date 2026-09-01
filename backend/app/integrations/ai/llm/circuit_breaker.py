"""Reactive companion to DailyCallBudget (budget.py). That one is a proactive cap
this app enforces on itself, checked *before* every call. These are reactive
breakers that remember when the *provider's own* quota already said no, so
FallbackLLMProvider doesn't pay for — and wait on — a network round trip that's
guaranteed to fail again: once a primary returns 429, every subsequent call skips
straight to the fallback for the rest of the cooldown instead of re-trying the
exhausted provider and hitting the same wall on every single job scored.

Two implementations, because "how long to cool down" genuinely differs by
provider:

- GeminiCircuitBreaker: Gemini's free-tier quota (see docs/matching-engine.md) is
  a *daily* cap ("GenerateRequestsPerDayPerProjectPerModel-FreeTier" per the
  API's own error detail), so the cooldown runs until the next UTC midnight
  rather than trusting the API's own `retryDelay` hint, which in practice has
  been observed to report a much shorter delay (tens of seconds) than the daily
  quota it's attached to actually resets on.
- FixedCooldownCircuitBreaker: for providers where a 429 during normal use is far
  more likely to be a short per-minute burst limit than a full daily cap — Groq's
  free tier, for instance, enforces both RPM and RPD limits, and a burst during a
  bulk "rescore all vacancies" run is the everyday case, not exhausting an entire
  day's quota. Parking every later call on a slower local fallback for the rest
  of the day over what was probably a transient burst would be overkill, so this
  uses a short, fixed cooldown instead (still a cooldown, not zero — a burst
  limit that just tripped is likely to trip again on an immediate retry too).
"""

from datetime import UTC, datetime, timedelta

import redis.asyncio as redis


def _seconds_until_next_utc_midnight(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


class GeminiCircuitBreaker:
    """Keyed by model, since the quota itself is per-model — a cooldown on
    gemini-2.0-flash must not silently also block a different configured model."""

    def __init__(self, redis_client: redis.Redis, model: str):
        self._redis = redis_client
        self._key = f"gemini_exhausted:{model}"

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


class FixedCooldownCircuitBreaker:
    """A RetryCircuitBreaker with a plain fixed cooldown after a failure — see
    the module docstring for when this fits better than GeminiCircuitBreaker's
    until-midnight cooldown. `key` should already identify the provider+model
    (e.g. "groq_exhausted:llama-3.3-70b-versatile") so unrelated providers never
    share a cooldown."""

    def __init__(self, redis_client: redis.Redis, key: str, cooldown_seconds: int):
        self._redis = redis_client
        self._key = key
        self._cooldown_seconds = cooldown_seconds

    async def is_open(self) -> bool:
        try:
            return bool(await self._redis.exists(self._key))
        except redis.RedisError:
            return False

    async def record_failure(self) -> None:
        try:
            await self._redis.set(self._key, "1", ex=self._cooldown_seconds)
        except redis.RedisError:
            pass
