"""Whether a scored match is worth interrupting someone for.

Two questions, both answerable from data the user set: is the score high enough,
and is it a reasonable hour. Nothing else — there is no digest tier, no
component-level bar and no urgency model, because none of those were ever
delivered and a threshold nobody can see is worse than no threshold.
"""

from dataclasses import dataclass

from app.domain.matching.models import JobMatch


@dataclass(frozen=True)
class NotificationPolicyConfig:
    enabled: bool = True
    # Send only for matches at or above this score. Defaults above the "apply"
    # band, so a notification means more than the jobs list already does.
    min_score: float = 75.0
    quiet_hours_start: int = 22
    quiet_hours_end: int = 8


class NotificationPolicy:
    def __init__(self, config: NotificationPolicyConfig | None = None):
        self._config = config or NotificationPolicyConfig()

    def should_notify(self, match: JobMatch, hour: int) -> bool:
        if not self._config.enabled or not match.eligible:
            return False
        if match.score < self._config.min_score:
            return False
        return not self.is_quiet_hours(hour)

    def is_quiet_hours(self, hour: int) -> bool:
        start, end = self._config.quiet_hours_start, self._config.quiet_hours_end
        if start == end:
            return False
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end
