"""Use case: apply NotificationPolicy to a JobMatch and dispatch it through the
configured provider. See docs/notifications.md.

Delivery is idempotent by construction: one notification row per (user, match,
channel), one delivery row per (notification, channel), and a match already
delivered is a no-op rather than a second message.
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
        """True if a message was sent, or had already been sent before."""
        hour = (now or datetime.now(UTC)).hour
        if not self._policy.should_notify(notification.match, hour):
            return False

        channel = NotificationChannel.TELEGRAM.value
        notification_id = await self._repository.get_or_create_notification(
            user_id, uuid.UUID(notification.match.id), channel
        )
        if await self._repository.has_successful_delivery(notification_id, channel):
            return True  # already delivered — never send twice

        try:
            await self._provider.send_job_match(notification)
        except Exception as exc:
            await self._repository.record_delivery(notification_id, channel, error=str(exc))
            raise
        await self._repository.record_delivery(notification_id, channel)
        return True
