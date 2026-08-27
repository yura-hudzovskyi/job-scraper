"""Notification records. Delivery must be idempotent — see docs/notifications.md."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class NotificationChannel(StrEnum):
    TELEGRAM = "telegram"
    EMAIL = "email"


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
