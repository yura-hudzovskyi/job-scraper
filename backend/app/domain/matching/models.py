"""Match result shapes. A score without a breakdown is a bug — see
docs/matching-engine.md ("Explainability is mandatory").
"""

from dataclasses import dataclass, field
from enum import StrEnum


class Recommendation(StrEnum):
    APPLY = "apply"
    CONSIDER = "consider"
    SKIP = "skip"


@dataclass(frozen=True)
class ScoreBreakdown:
    skills: float
    role: float
    experience: float
    semantic_fit: float
    salary: float
    location: float
    transferable_skills: float
    preferences: float


@dataclass(frozen=True)
class MatchReason:
    label: str
    detail: str


@dataclass(frozen=True)
class MatchGap:
    label: str
    critical: bool


@dataclass(frozen=True)
class JobMatch:
    id: str
    user_id: str
    canonical_job_id: str

    eligible: bool
    requirement_match: float
    practical_fit: float
    breakdown: ScoreBreakdown

    strengths: list[MatchReason] = field(default_factory=list)
    gaps: list[MatchGap] = field(default_factory=list)

    recommendation: Recommendation | None = None
    recommended_cv_variant: str | None = None
    llm_summary: str | None = None
