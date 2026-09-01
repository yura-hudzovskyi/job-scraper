"""A hard daily ceiling on how many LLM calls each capability may spend, counted
in Redis — see docs/ai-pipeline-v3.md (F4).

Separate counters per capability *are* the interactive reserve: a backlog run
burning through job extraction can't touch what CV analysis has left, because
they never share a budget. That's the whole mechanism, and it's why the limits
live per capability rather than as one pool with percentages carved out of it.

This is proactive, and deliberately independent of whatever the provider's own
quota does: a free tier can change under us, and a billing-enabled account may
not hard-stop at all. The router checks it before choosing a leg, so an
exhausted budget costs no network round trip.

Fails open on a Redis error, same contract as the rest of the optional AI layer:
a broker hiccup must not stop work that a provider could still serve — Celery
already needs the same Redis to run at all.
"""

import logging
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis

from app.integrations.ai.routing.errors import seconds_until_next_utc_midnight

logger = logging.getLogger(__name__)

_KEY_TTL_SECONDS = 2 * 24 * 60 * 60  # generous cleanup margin past one UTC day


class DailyCapabilityBudget:
    def __init__(self, redis_client: redis.Redis, capability: str, daily_limit: int):
        self._redis = redis_client
        self._capability = capability
        self._daily_limit = daily_limit

    def _key(self) -> str:
        return f"ai:budget:{self._capability}:{datetime.now(UTC):%Y-%m-%d}"

    async def try_consume(self) -> bool:
        """Counts this call and says whether it was still within today's budget."""
        try:
            count = await self._redis.incr(self._key())
            if count == 1:
                await self._redis.expire(self._key(), _KEY_TTL_SECONDS)
        except redis.RedisError:
            logger.warning("could not read the %s budget — allowing the call", self._capability)
            return True
        return count <= self._daily_limit

    async def retry_after(self) -> timedelta:
        """Budgets are daily, so the answer is always "after the next UTC
        midnight" — the reset a caller can schedule against."""
        return seconds_until_next_utc_midnight()

    async def used(self) -> int:
        """For the System page: how much of today's budget is gone."""
        try:
            raw = await self._redis.get(self._key())
        except redis.RedisError:
            return 0
        return int(raw) if raw else 0

    @property
    def daily_limit(self) -> int:
        return self._daily_limit
