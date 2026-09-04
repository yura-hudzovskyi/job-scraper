"""The relay that moves outbox events onto the queue, and purges delivered ones.

What is deliberately *not* here yet: a handler for `document_revision_created`.
The extractor that consumes it arrives in Phase 3, and inventing a handler now
would be a placeholder in a path claimed complete.

That does not make this relay a no-op. The guarantee it exists to provide is
about delivery — an event written in a committed transaction is picked up,
handed to whatever is registered, and marked published exactly once per success,
with failures counted rather than lost. That property is real and testable with
an empty registry, and Phase 3 registers a handler without touching any of it.

An event with no handler is still delivered: it is marked published and counted.
The alternative, leaving it pending, would build a backlog of events nobody is
ever going to want and hide a genuinely stuck one among them.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.session import session_scope
from app.repositories.outbox_repository import OutboxRepository
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]

# event_type -> handler. Phase 3 registers "document_revision_created" here.
HANDLERS: dict[str, EventHandler] = {}

# How long a delivered event is kept. Long enough to answer "did that ingest
# actually emit an event" during an incident, short enough that the table does
# not grow forever.
PUBLISHED_RETENTION_DAYS = 7

BATCH_SIZE = 100


async def _relay() -> dict[str, int]:
    published = failed = unhandled = 0

    async with session_scope() as session:
        outbox = OutboxRepository(session)
        events = await outbox.unpublished(BATCH_SIZE)
        delivered: list[int] = []

        for event in events:
            handler = HANDLERS.get(event.event_type)
            if handler is None:
                unhandled += 1
                delivered.append(event.id)
                continue
            try:
                await handler(event.aggregate_id, event.payload)
            except Exception as exc:
                # One event that will not publish must not block the ones behind
                # it: the failure is counted on its own row and the relay moves on.
                failed += 1
                logger.warning(
                    "outbox event %d (%s) failed to publish", event.id, event.event_type,
                    exc_info=True,
                )
                await outbox.record_failure(event.id, str(exc))
                continue
            delivered.append(event.id)
            published += 1

        await outbox.mark_published(delivered)
        pending = await outbox.count_pending()

    return {
        "published": published,
        "unhandled": unhandled,
        "failed": failed,
        "pending": pending,
    }


async def _purge() -> int:
    async with session_scope() as session:
        cutoff = datetime.now(UTC) - timedelta(days=PUBLISHED_RETENTION_DAYS)
        return await OutboxRepository(session).purge_published(cutoff)


@celery_app.task(name="outbox.relay")
def relay() -> dict[str, int]:
    return asyncio.run(_relay())


@celery_app.task(name="outbox.purge_published")
def purge_published() -> dict[str, int]:
    return {"purged": asyncio.run(_purge())}
