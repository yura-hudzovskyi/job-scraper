"""Notification channel abstraction. See docs/notifications.md."""

from typing import Protocol

from app.domain.notifications.models import JobMatchNotification


class NotificationProvider(Protocol):
    async def send_job_match(self, notification: JobMatchNotification) -> None: ...
