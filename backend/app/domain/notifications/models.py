"""Notification records. Delivery must be idempotent — see docs/notifications.md."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.jobs.models import SalaryRange
from app.domain.matching.models import JobMatch


class NotificationChannel(StrEnum):
    TELEGRAM = "telegram"


@dataclass(frozen=True)
class JobMatchNotification:
    """A JobMatch plus the display fields a provider needs to render it — the
    match itself only carries ids and numbers, not title/company/salary/links.

    source_links is (source name, url) pairs, so a vacancy seen on both DOU and
    Djinni links out to each by name instead of one arbitrarily-chosen URL."""

    match: JobMatch
    job_title: str
    company: str
    source_links: list[tuple[str, str]] = field(default_factory=list)
    salary: SalaryRange | None = None
    seniority: str | None = None
    remote: bool = False
    # Running Approve/Reject totals across this user's matches, shown as a small
    # progress footer on the card. Includes this match itself.
    pending_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0


@dataclass(frozen=True)
class Notification:
    id: str
    user_id: str
    job_match_id: str
    channel: NotificationChannel
    created_at: datetime
