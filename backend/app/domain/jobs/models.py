"""Raw -> Normalized -> Canonical job shapes. See docs/domain-model.md.

Downstream modules (matching, dedup, notifications) must only ever depend on
NormalizedJob or CanonicalJob — never on a source's raw payload shape.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"


class JobChangeType(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    CLOSED = "closed"
    REOPENED = "reopened"


@dataclass(frozen=True)
class RawJob:
    source: str
    external_id: str
    url: str
    payload: dict[str, Any]
    fetched_at: datetime


@dataclass(frozen=True)
class SalaryRange:
    min: float | None
    max: float | None
    currency: str | None
    gross: bool | None = None


@dataclass(frozen=True)
class JobLocation:
    remote: bool
    countries: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedJobSkill:
    name: str
    required: bool


@dataclass(frozen=True)
class NormalizedJob:
    source: str
    external_id: str
    url: str
    title: str
    company: str
    description: str
    employment_type: EmploymentType
    location: JobLocation
    salary: SalaryRange | None
    seniority: str | None
    required_experience_years: float | None
    skills: list[NormalizedJobSkill] = field(default_factory=list)
    skills_extracted_by: str | None = None  # which LLM extracted `skills`, if any


@dataclass(frozen=True)
class CanonicalJob:
    """One real-world vacancy, potentially backed by several source records."""

    id: str
    normalized: NormalizedJob
    source_records: list[str] = field(default_factory=list)  # JobSourceRecord ids
