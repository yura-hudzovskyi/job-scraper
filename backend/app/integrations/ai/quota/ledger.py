"""An append-only record of every LLM call the router makes — see
docs/ai-pipeline-v3.md (6.1, 7).

Budgets answer "may I spend one more call today". This answers the questions that
only history can: which capability is actually burning the quota, how often a leg
is failing and with what, whether the limits in Settings are anywhere near
reality. Without it, tuning those numbers is guesswork.

Writes go to Redis, not Postgres, on purpose: the router runs inside providers
and Celery tasks that don't all have a database session, and an audit write must
never slow down or fail the call it is describing. A periodic task drains the
buffer into `ai_invocations` (app/workers/tasks/ai_ledger.py), so the durable
history is Postgres and the hot path is one Redis push.

The buffer is capped. If the drain stops running, the newest records win and the
oldest are dropped rather than growing until Redis falls over — losing audit rows
is survivable, losing the broker is not.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis

logger = logging.getLogger(__name__)

_KEY = "ai:invocations"
_MAX_BUFFERED = 5000

OK = "ok"


@dataclass(frozen=True)
class InvocationRecord:
    capability: str
    provider: str
    model: str
    # "ok", or the FailureKind that ended the attempt.
    outcome: str
    latency_ms: int
    # A proxy for cost that needs no vendor-specific usage parsing: providers
    # report tokens in four different shapes, and a length this app measured
    # itself is honest about being an approximation.
    prompt_chars: int
    status: int | None = None
    at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["at"] = (self.at or datetime.now(UTC)).isoformat()
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "InvocationRecord":
        at = payload.get("at")
        return cls(
            capability=payload["capability"],
            provider=payload["provider"],
            model=payload["model"],
            outcome=payload["outcome"],
            latency_ms=payload["latency_ms"],
            prompt_chars=payload["prompt_chars"],
            status=payload.get("status"),
            at=datetime.fromisoformat(at) if at else None,
        )


class InvocationLog:
    def __init__(self, redis_client: redis.Redis, key: str = _KEY):
        self._redis = redis_client
        self._key = key

    async def record(self, record: InvocationRecord) -> None:
        """Never raises: an audit trail that can break a working call is worse
        than no audit trail."""
        try:
            async with self._redis.pipeline() as pipe:
                pipe.rpush(self._key, json.dumps(record.to_payload()))
                pipe.ltrim(self._key, -_MAX_BUFFERED, -1)
                await pipe.execute()
        except redis.RedisError:
            logger.warning("could not record an AI invocation — continuing")

    async def drain(self, limit: int = 500) -> list[InvocationRecord]:
        """Takes up to `limit` records off the buffer. Anything unparseable is
        dropped rather than blocking the drain forever."""
        try:
            raw = await self._redis.lpop(self._key, limit)
        except redis.RedisError:
            logger.warning("could not drain the AI invocation buffer")
            return []
        if not raw:
            return []

        entries = raw if isinstance(raw, list) else [raw]
        records = []
        for entry in entries:
            try:
                records.append(InvocationRecord.from_payload(json.loads(entry)))
            except (ValueError, KeyError, TypeError):
                logger.warning("dropping an unreadable AI invocation record")
        return records
