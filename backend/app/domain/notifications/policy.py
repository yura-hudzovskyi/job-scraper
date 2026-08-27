"""Decides whether/when a JobMatch should be delivered, and through which channel.

Score-band thresholds and quiet hours as described in docs/notifications.md.
"""

from dataclasses import dataclass

from app.domain.matching.models import JobMatch


@dataclass(frozen=True)
class NotificationPolicyConfig:
    immediate_threshold: float = 85.0
    conditional_threshold: float = 75.0
    digest_threshold: float = 65.0
    quiet_hours_start: int = 22
    quiet_hours_end: int = 8


class NotificationPolicy:
    def __init__(self, config: NotificationPolicyConfig | None = None):
        self._config = config or NotificationPolicyConfig()

    def should_notify_immediately(self, match: JobMatch) -> bool:
        raise NotImplementedError

    def should_include_in_digest(self, match: JobMatch) -> bool:
        raise NotImplementedError

    def is_quiet_hours(self, hour: int) -> bool:
        raise NotImplementedError
