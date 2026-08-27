"""Application tracker state machine, used for conversion analytics
(e.g. "52 applications sent, 41 no response, 5 HR, 3 technical, 0 offers")."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ApplicationStatus(StrEnum):
    DISCOVERED = "discovered"
    SAVED = "saved"
    APPLIED = "applied"
    HR_SCREEN = "hr_screen"
    TECHNICAL = "technical"
    TEST_TASK = "test_task"
    FINAL = "final"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class Application:
    id: str
    user_id: str
    canonical_job_id: str
    status: ApplicationStatus
    cv_variant: str | None
    applied_at: datetime | None
    updated_at: datetime
