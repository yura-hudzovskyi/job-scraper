"""Raw -> Normalized -> Canonical job shapes. See docs/domain-model.md.

Downstream modules (matching, dedup, notifications) must only ever depend on
NormalizedJob or CanonicalJob — never on a source's raw payload shape.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.domain.categories import JobCategory


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


class RequirementType(StrEnum):
    """How the posting framed a skill — see docs/ai-pipeline-v3.md (E2). The
    distinction that matters downstream: a missing REQUIRED_* is a real gap, a
    missing OPTIONAL_EXPLICIT/CONTEXT is not, and UNKNOWN is not a claim at all,
    so it must never be reported as a confirmed gap."""

    REQUIRED_EXPLICIT = "required_explicit"
    REQUIRED_INFERRED = "required_inferred"
    OPTIONAL_EXPLICIT = "optional_explicit"
    CONTEXT = "context"
    UNKNOWN = "unknown"


_REQUIRED_TYPES = (RequirementType.REQUIRED_EXPLICIT, RequirementType.REQUIRED_INFERRED)


@dataclass(frozen=True)
class NormalizedJobSkill:
    name: str  # the ontology's display name when it knows this skill (app/domain/skills)
    requirement: RequirementType = RequirementType.UNKNOWN
    canonical_id: str | None = None
    evidence: str | None = None  # verbatim quote from the posting backing the framing
    confidence: float | None = None

    @property
    def required(self) -> bool:
        """Scoring only ever asks the yes/no question; everything that explains a
        result reads `requirement` instead."""
        return self.requirement in _REQUIRED_TYPES


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
    # What kind of role this is, from the same extraction call. A ranking signal,
    # not a filter — see app/domain/categories.py.
    category: JobCategory | None = None
    category_confidence: float | None = None


@dataclass(frozen=True)
class CanonicalJob:
    """One real-world vacancy, potentially backed by several source records."""

    id: str
    normalized: NormalizedJob
    source_records: list[str] = field(default_factory=list)  # JobSourceRecord ids
