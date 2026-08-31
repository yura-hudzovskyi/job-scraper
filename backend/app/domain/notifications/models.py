"""Notification records. Delivery must be idempotent — see docs/notifications.md."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.jobs.models import SalaryRange
from app.domain.matching.models import JobMatch


class NotificationChannel(StrEnum):
    TELEGRAM = "telegram"
    EMAIL = "email"


@dataclass(frozen=True)
class JobMatchNotification:
    """A JobMatch plus the display fields a provider needs to render it —
    JobMatch itself only carries ids/scores, not title/company/salary/links.

    source_links is (source name, url) pairs — a job seen on both DOU and Djinni
    carries both, so the message can link out to each by name instead of picking
    one URL arbitrarily (see JobRepository.list_source_links_for_canonical)."""

    match: JobMatch
    job_title: str
    company: str
    source_links: list[tuple[str, str]] = field(default_factory=list)
    salary: SalaryRange | None = None
    seniority: str | None = None
    required_experience_years: float | None = None
    remote: bool = False


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
