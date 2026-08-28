"""Use case: apply NotificationPolicy to a JobMatch and dispatch through the
configured NotificationProvider. See docs/notifications.md.

Only immediate delivery is implemented here. The daily-digest band
(NotificationPolicy.should_include_in_digest) is recognized but not yet delivered —
that needs a scheduled aggregation job this doesn't build yet.
"""

import uuid
from datetime import UTC, datetime

from app.domain.notifications.models import JobMatchNotification, NotificationChannel
from app.domain.notifications.policy import NotificationPolicy
from app.integrations.notifications.base import NotificationProvider
from app.repositories.notification_repository import NotificationRepository


class NotificationService:
    def __init__(
        self,
        policy: NotificationPolicy,
        provider: NotificationProvider,
        repository: NotificationRepository,
    ):
        self._policy = policy
        self._provider = provider
        self._repository = repository

    async def notify_if_relevant(
        self,
        user_id: uuid.UUID,
        notification: JobMatchNotification,
        now: datetime | None = None,
    ) -> bool:
        """Returns True if a notification was sent (or already had been)."""
        match = notification.match
        hour = (now or datetime.now(UTC)).hour

        if not self._policy.should_notify_immediately(match) or self._policy.is_quiet_hours(hour):
            return False

        channel = NotificationChannel.TELEGRAM.value
        notification_id = await self._repository.get_or_create_notification(
            user_id, uuid.UUID(match.id), channel
        )
        if await self._repository.has_successful_delivery(notification_id, channel):
            return True  # already delivered — idempotent no-op, never send twice

        try:
            await self._provider.send_job_match(notification)
        except Exception as exc:
            await self._repository.record_delivery(notification_id, channel, error=str(exc))
            raise
        else:
            await self._repository.record_delivery(notification_id, channel)
            return True
