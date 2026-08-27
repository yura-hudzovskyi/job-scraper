"""Candidate profile and preferences.

`CandidateProfile` is what the candidate has actually done (derived from CVs).
`UserPreference` is what the candidate wants (edited directly). Never merge the two —
see docs/domain-model.md.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class SkillLevel(StrEnum):
    AWARE = "aware"
    COMMERCIAL = "commercial"
    STRONG = "strong"
    EXPERT = "expert"


@dataclass(frozen=True)
class CandidateSkill:
    name: str
    level: SkillLevel
    years: float | None = None


@dataclass(frozen=True)
class ExperienceEntry:
    company: str
    title: str
    start_date: str
    end_date: str | None
    description: str
    skills: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateProfile:
    id: str
    user_id: str
    experience_years: float
    roles: list[str]
    skills: list[CandidateSkill]
    experience: list[ExperienceEntry] = field(default_factory=list)
    achievements: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    ai_experience: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UserPreference:
    user_id: str
    desired_salary_usd: int | None
    preferred_roles: list[str] = field(default_factory=list)
    preferred_stack: list[str] = field(default_factory=list)
    acceptable_stack: list[str] = field(default_factory=list)
    blocked_stack: list[str] = field(default_factory=list)
    work_formats: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    max_required_experience: float | None = None
    industries_blacklist: list[str] = field(default_factory=list)
    companies_blacklist: list[str] = field(default_factory=list)
