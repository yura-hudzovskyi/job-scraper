"""Persistence for the transactional outbox.

`append` deliberately does not flush or commit. It joins whatever transaction the
caller is already in, which is the entire point — an event that commits
separately from the state change it describes has reintroduced the gap the
outbox exists to close.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from app.db.models.outbox import OutboxEventModel
from app.repositories.base import rows_affected

# How many times the relay retries one event before it stops being picked up. It
# stays in the table with its error, visible, rather than being deleted or
# retried forever.
MAX_ATTEMPTS = 10


class OutboxRepository:
    def __init__(self, session: Any):
        self._session = session

    def append(
        self,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record an event in the caller's transaction.

        Synchronous and without a flush on purpose: this must be indistinguishable
        from the surrounding state change, committing with it or rolling back
        with it.
        """
        self._session.add(
            OutboxEventModel(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=payload or {},
            )
        )

    async def unpublished(self, limit: int = 100) -> list[OutboxEventModel]:
        """The oldest events still awaiting publication.

        Ordered by id, which is insertion order: events about one aggregate have
        to reach a handler in the order they happened, or a revision could be
        reported parsed before it is reported created.
        """
        result = await self._session.execute(
            select(OutboxEventModel)
            .where(
                OutboxEventModel.published_at.is_(None),
                OutboxEventModel.attempts < MAX_ATTEMPTS,
            )
            .order_by(OutboxEventModel.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def mark_published(self, event_ids: list[int]) -> int:
        if not event_ids:
            return 0
        published_at = datetime.now(UTC)
        for event_id in event_ids:
            event = await self._session.get(OutboxEventModel, event_id)
            if event is not None:
                event.published_at = published_at
        await self._session.flush()
        return len(event_ids)

    async def record_failure(self, event_id: int, error: str) -> None:
        """Count an attempt against an event that would not publish. Truncated
        because an exception's text can be a page long and this column exists to
        identify the problem, not to reproduce it."""
        event = await self._session.get(OutboxEventModel, event_id)
        if event is None:
            return
        event.attempts += 1
        event.last_error = error[:500]
        await self._session.flush()

    async def count_pending(self) -> int:
        """Backlog depth — the number the System page would show if this ever
        stops draining."""
        result = await self._session.execute(
            select(OutboxEventModel.id).where(
                OutboxEventModel.published_at.is_(None),
                OutboxEventModel.attempts < MAX_ATTEMPTS,
            )
        )
        return len(list(result.scalars()))

    async def purge_published(self, before: datetime) -> int:
        """Drop events already delivered and older than `before`.

        This table grows with every ingest, so it ships with its own cleanup
        rather than waiting for someone to notice. Unpublished events are never
        purged regardless of age — an old undelivered event is a bug to look at,
        not garbage to collect.
        """
        result = await self._session.execute(
            delete(OutboxEventModel).where(
                OutboxEventModel.published_at.is_not(None),
                OutboxEventModel.published_at < before,
            )
        )
        await self._session.flush()
        return rows_affected(result)
