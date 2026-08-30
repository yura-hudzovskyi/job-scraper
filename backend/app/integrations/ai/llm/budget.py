"""A hard, explicit ceiling on how many times an expensive/quota-limited
operation may run per day — independent of whatever the provider's own rate-limit
or billing behavior is. Built for LlmReranker (app/domain/matching/llm_reranker.py):
FallbackLLMProvider's existing 429->Ollama fallback only degrades quality on a
Gemini rate-limit response, it doesn't actually protect the free-tier quota, and
doesn't protect against a billing-enabled Google Cloud project not hard-capping
at 429 at all. This gives a direct, user-configurable cap instead.
"""

from datetime import UTC, datetime

import redis.asyncio as redis

_KEY_TTL_SECONDS = 2 * 24 * 60 * 60  # generous cleanup margin past one UTC day


class DailyCallBudget:
    def __init__(self, redis_client: redis.Redis, key_prefix: str, daily_limit: int):
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._daily_limit = daily_limit

    async def try_consume(self) -> bool:
        """Atomically increments today's counter and returns whether this call is
        still within budget. Fails open on a Redis error — an optional quality
        layer shouldn't block scoring over a broker hiccup, and Celery itself
        already depends on the same Redis being reachable to run at all."""
        key = f"{self._key_prefix}:{datetime.now(UTC):%Y-%m-%d}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, _KEY_TTL_SECONDS)
        except redis.RedisError:
            return True
        return count <= self._daily_limit
