"""Notification channel abstraction. See docs/notifications.md."""

from typing import Protocol

from app.domain.matching.models import JobMatch


class NotificationProvider(Protocol):
    async def send_job_match(self, match: JobMatch) -> None: ...
