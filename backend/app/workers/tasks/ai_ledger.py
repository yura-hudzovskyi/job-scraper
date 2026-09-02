"""Moves the AI call ledger from Redis into Postgres, on a schedule.

The router records every call into a capped Redis list so the hot path stays one
push (see app/integrations/ai/quota/ledger.py). This drains that buffer into
`ai_invocations` and prunes history past the window anyone would look at. Losing
a drain cycle costs nothing: the buffer keeps filling, and the next run picks it
up.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis

from app.config.settings import get_settings
from app.db.session import session_scope
from app.integrations.ai.quota.ledger import InvocationLog
from app.repositories.ai_invocation_repository import AiInvocationRepository
from app.workers.celery_app import celery_app

# One drain is bounded so a backlog can't turn into one enormous transaction; the
# next tick takes the rest.
_DRAIN_LIMIT = 2000
_RETENTION_DAYS = 30


async def _run() -> dict[str, int]:
    settings = get_settings()
    log = InvocationLog(redis.from_url(settings.redis_url))
    records = await log.drain(_DRAIN_LIMIT)

    async with session_scope() as session:
        repository = AiInvocationRepository(session)
        stored = await repository.insert_many(records)
        pruned = await repository.delete_older_than(
            datetime.now(UTC) - timedelta(days=_RETENTION_DAYS)
        )
    return {"stored": stored, "pruned": pruned}


@celery_app.task(name="ai_ledger.flush")
def flush_ai_invocations() -> dict[str, int]:
    return asyncio.run(_run())
