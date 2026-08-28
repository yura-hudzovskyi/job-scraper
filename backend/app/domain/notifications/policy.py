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
    # "salary/location also match" bar for the conditional (75-84) band.
    strong_component_threshold: float = 90.0
    quiet_hours_start: int = 22
    quiet_hours_end: int = 8


class NotificationPolicy:
    def __init__(self, config: NotificationPolicyConfig | None = None):
        self._config = config or NotificationPolicyConfig()

    def should_notify_immediately(self, match: JobMatch) -> bool:
        if not match.eligible:
            return False
        if match.practical_fit >= self._config.immediate_threshold:
            return True
        if match.practical_fit >= self._config.conditional_threshold:
            return (
                match.breakdown.salary >= self._config.strong_component_threshold
                and match.breakdown.location >= self._config.strong_component_threshold
            )
        return False

    def should_include_in_digest(self, match: JobMatch) -> bool:
        if not match.eligible:
            return False
        return self._config.digest_threshold <= match.practical_fit < self._config.conditional_threshold

    def is_quiet_hours(self, hour: int) -> bool:
        start, end = self._config.quiet_hours_start, self._config.quiet_hours_end
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end
