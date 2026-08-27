"""Use case: apply NotificationPolicy to a JobMatch and dispatch through the
configured NotificationProvider(s)."""

from app.domain.matching.models import JobMatch
from app.domain.notifications.policy import NotificationPolicy
from app.integrations.notifications.base import NotificationProvider


class NotificationService:
    def __init__(self, policy: NotificationPolicy, provider: NotificationProvider):
        self._policy = policy
        self._provider = provider

    async def notify_if_relevant(self, match: JobMatch) -> None:
        raise NotImplementedError
