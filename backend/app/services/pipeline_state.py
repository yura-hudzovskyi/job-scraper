"""Whether a long pipeline run is currently in flight.

Rebuilding embeddings, rescoring every vacancy and running retrieval are minutes-
to-hours of background work over the whole corpus. Two of them racing produces
half-written state and a doubled provider bill, and the UI can't prevent that on
its own — a second browser tab, a stale page or a curl call all bypass a disabled
button. So the claim lives server-side: starting a stage takes a lock, and a
second attempt is refused rather than queued.

Every lock carries a TTL. A worker that dies mid-run would otherwise leave a
stage "running" forever with no way to clear it short of a Redis command, which
is exactly the kind of state nobody can debug from the UI. Long runs refresh the
TTL as they go, so the expiry only fires when the work really stopped.

Reads fail open: if Redis can't answer, the UI shows "not running" rather than
locking the operator out of their own pipeline over a broker hiccup.
"""

import logging
from enum import StrEnum

import redis.asyncio as redis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ai:pipeline:running"
# Long enough that a slow batch doesn't expire mid-run, short enough that a dead
# worker unblocks the operator within an hour.
_DEFAULT_TTL_SECONDS = 3600
# A fan-out has no completion event, so its flag is kept alive by the work itself
# and lapses shortly after the queue drains.
FANOUT_TTL_SECONDS = 600


class PipelineStage(StrEnum):
    EMBEDDINGS = "embeddings"
    SCORING = "scoring"
    RETRIEVAL = "retrieval"


class PipelineRunState:
    def __init__(self, redis_client: redis.Redis, ttl_seconds: int = _DEFAULT_TTL_SECONDS):
        self._redis = redis_client
        self._ttl = ttl_seconds

    def _key(self, stage: PipelineStage) -> str:
        return f"{_KEY_PREFIX}:{stage.value}"

    async def start(self, stage: PipelineStage, ttl_seconds: int | None = None) -> bool:
        """Take the lock. False means this stage is already running — the caller
        should tell the user, not start a second pass."""
        try:
            acquired = await self._redis.set(
                self._key(stage), "1", ex=ttl_seconds or self._ttl, nx=True
            )
        except redis.RedisError:
            # Better to run than to refuse work because the broker blinked; the
            # tasks themselves are idempotent.
            logger.warning("could not take the %s pipeline lock — proceeding", stage.value)
            return True
        return bool(acquired)

    async def heartbeat(self, stage: PipelineStage, ttl_seconds: int | None = None) -> None:
        """Push the expiry out. Called per batch so a genuinely long run never
        looks abandoned — and, for a stage that fans out into thousands of
        independent tasks and therefore has no single finish line, it is the only
        thing keeping the flag alive: it lapses once the work stops happening."""
        try:
            await self._redis.expire(self._key(stage), ttl_seconds or self._ttl)
        except redis.RedisError:
            logger.warning("could not refresh the %s pipeline lock", stage.value)

    async def finish(self, stage: PipelineStage) -> None:
        try:
            await self._redis.delete(self._key(stage))
        except redis.RedisError:
            logger.warning("could not release the %s pipeline lock — it expires on its own", stage.value)

    async def running(self) -> dict[str, bool]:
        states: dict[str, bool] = {}
        for stage in PipelineStage:
            try:
                states[stage.value] = bool(await self._redis.exists(self._key(stage)))
            except redis.RedisError:
                states[stage.value] = False
        return states
