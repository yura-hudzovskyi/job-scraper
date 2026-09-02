"""Match result shapes. A score without a breakdown is a bug — see
docs/matching-engine.md ("Explainability is mandatory").
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.matching.provenance import MatchProvenance


class Recommendation(StrEnum):
    APPLY = "apply"
    CONSIDER = "consider"
    SKIP = "skip"


class MatchDecision(StrEnum):
    """The user's own swipe-style verdict on a match — independent of and never
    overwritten by `Recommendation` (the pipeline's own opinion). Set via the
    Telegram Approve/Reject buttons (see telegram_provider.py and the webhook
    route in api/routes/telegram.py); stays PENDING for matches never delivered
    or not yet acted on."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


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
    # How much evidence stood behind the score, 0-1 — see hybrid.py. Deliberately
    # separate from the score: an 84 backed by two extracted requirements and an
    # 84 backed by twelve are not the same claim.
    confidence: float | None = None
    # What the result could not establish. Never gaps — see HybridMatchEngine.
    risks: list[str] = field(default_factory=list)
    llm_assessment: LlmAssessment | None = None
    # How this result was produced — engine, analysis level, the CV/job revisions
    # it was scored against, the models involved. See provenance.py; None only for
    # a match built outside the pipeline (tests, or a row stored before v3).
    provenance: MatchProvenance | None = None
    scored_at: datetime | None = None  # bumped on every rescore — lets the UI detect "rescore finished"
    decision: MatchDecision = MatchDecision.PENDING
