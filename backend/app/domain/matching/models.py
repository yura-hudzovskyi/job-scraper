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
class LlmAssessment:
    """The LLM's qualitative "should I apply?" verdict over an already-scored
    match — see LlmReranker (app/domain/matching/llm_reranker.py) and
    docs/matching-engine.md's Phase 4 section. Only ever populated for
    Recommendation.APPLY matches (see MatchingService.should_i_apply) — a
    "second opinion" layered on top of the deterministic score, never a
    replacement for it.
    """

    overall_fit: float
    recommendation: Recommendation
    confidence: float
    strengths: list[str]
    gaps: list[str]
    critical_gaps: list[str]
    transferable_experience: list[str]
    interview_risk: str
    summary: str
    recommended_cv: str | None
    model_label: str


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
    llm_assessment: LlmAssessment | None = None
    skills_source: str | None = None  # which LLM extracted this job's skills, if any
    scored_by: str | None = None  # "AI (<model>)" when AiMatcher produced this score, else "deterministic"
