"""Which provider legs are usable right now, and until when they aren't.

Replaces the two hand-written circuit breakers with one store that keeps the
*reason* alongside the cooldown: the System page can then say "Groq is rate
limited for another 40s" instead of a bare "cooling down", and the router can
tell "come back in a minute" apart from "this key is broken".

Reads fail open and writes fail silent, same contract the breakers had: a Redis
hiccup must never block a call the provider could still serve, and must never
turn into an error in a code path whose whole job is degrading gracefully.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis

from app.integrations.ai.routing.errors import FailureKind, ProviderFailure

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ai:leg"


@dataclass(frozen=True)
class LegState:
    available: bool
    cooldown_until: datetime | None = None
    reason: FailureKind | None = None

    @property
    def retry_after(self) -> timedelta | None:
        if self.cooldown_until is None:
            return None
        return max(timedelta(0), self.cooldown_until - datetime.now(UTC))


_AVAILABLE = LegState(available=True)


class ProviderStateStore:
    def __init__(self, redis_client: redis.Redis, key_prefix: str = _KEY_PREFIX):
        self._redis = redis_client
        self._key_prefix = key_prefix

    def _key(self, leg_key: str) -> str:
        return f"{self._key_prefix}:{leg_key}"

    async def state(self, leg_key: str) -> LegState:
        key = self._key(leg_key)
        try:
            async with self._redis.pipeline() as pipe:
                pipe.get(key)
                pipe.ttl(key)
                reason, ttl = await pipe.execute()
        except redis.RedisError:
            logger.warning("could not read provider state for %s — assuming available", leg_key)
            return _AVAILABLE

        if reason is None:
            return _AVAILABLE
        kind = _to_kind(reason)
        # A key with no TTL shouldn't exist (every write sets one); treat it as a
        # short cooldown rather than an eternal outage.
        seconds = ttl if isinstance(ttl, int) and ttl > 0 else 1
        return LegState(
            available=False,
            cooldown_until=datetime.now(UTC) + timedelta(seconds=seconds),
            reason=kind,
        )

    async def record_failure(self, leg_key: str, failure: ProviderFailure) -> None:
        """Park this leg for exactly as long as the failure says. A schema failure
        parks nothing — the provider is fine, its answer wasn't."""
        cooldown = int(failure.cooldown.total_seconds())
        if cooldown <= 0:
            return
        try:
            await self._redis.set(self._key(leg_key), failure.kind.value, ex=cooldown)
        except redis.RedisError:
            logger.warning("could not record provider state for %s", leg_key)

    async def record_success(self, leg_key: str) -> None:
        """A successful call means whatever we were waiting out is over — most
        visibly after a transient blip, where the default cooldown is a guess."""
        try:
            await self._redis.delete(self._key(leg_key))
        except redis.RedisError:
            logger.warning("could not clear provider state for %s", leg_key)


def _to_kind(raw: object) -> FailureKind | None:
    value = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        return FailureKind(value)
    except ValueError:
        return None
