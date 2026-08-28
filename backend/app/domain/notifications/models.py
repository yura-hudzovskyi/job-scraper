"""Notification records. Delivery must be idempotent — see docs/notifications.md."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.matching.models import JobMatch


class NotificationChannel(StrEnum):
    TELEGRAM = "telegram"
    EMAIL = "email"


@dataclass(frozen=True)
class JobMatchNotification:
    """A JobMatch plus the display fields a provider needs to render it —
    JobMatch itself only carries ids/scores, not title/company/url."""

    match: JobMatch
    job_title: str
    company: str
    job_url: str


@dataclass(frozen=True)
class Notification:
    id: str
    user_id: str
    job_match_id: str
    channel: NotificationChannel
    created_at: datetime


@dataclass(frozen=True)
class NotificationDelivery:
    notification_id: str
    channel: NotificationChannel
    delivered_at: datetime | None
    error: str | None = None
