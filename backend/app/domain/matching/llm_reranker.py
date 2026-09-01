"""Phase 4 — LLM reranking / "should I apply?" (docs/matching-engine.md). Only
ever called for matches the deterministic pipeline already recommends CONSIDER or
APPLY (see MatchingService.should_i_apply) — the LLM reasons on top of an
already-decent match, catching nuance a formula structurally can't (seniority
mismatch, on-call burden, etc.), not re-deriving fit from scratch. Same
_Extracted*(BaseModel) + prompt-template + structured_completion pattern as
job_skill_extraction_service.py / cv_service.py.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.candidates.models import CandidateProfile
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.models import (
    LlmAssessment,
    MatchGap,
    MatchReason,
    Recommendation,
    ScoreBreakdown,
)
from app.integrations.ai.llm.base import LLMProvider
from app.integrations.ai.llm.budget import DailyCallBudget


class _LlmVerdict(BaseModel):
    overall_fit: float
    recommendation: Literal["apply", "consider", "skip"]
    confidence: float
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    critical_gaps: list[str] = Field(default_factory=list)
    transferable_experience: list[str] = Field(default_factory=list)
    interview_risk: Literal["low", "medium", "high"]
    summary: str
    recommended_cv: str | None = None


_PROMPT = """You are helping a candidate decide whether to apply to a job. A \
deterministic scoring pipeline already found this at least a decent match — your \
job is to add judgment the pipeline can't: seniority fit, day-to-day realities, and \
gaps that matter beyond a skills checklist.

Job title: {job_title}
Company: {company}
Job description:
---
{job_description}
---

Candidate:
- Roles: {candidate_roles}
- Skills: {candidate_skills}
- Experience: {candidate_experience}
- Achievements: {candidate_achievements}

Deterministic score already computed (0-100 each):
- Skills {skills:.0f}, Role {role:.0f}, Experience {experience:.0f}, \
Semantic fit {semantic_fit:.0f}, Salary {salary:.0f}, Location {location:.0f}, \
Transferable skills {transferable_skills:.0f}, Preferences {preferences:.0f}
- Strengths already identified: {strengths}
- Gaps already identified: {gaps}

Give your own independent assessment: overall_fit (0-100), recommendation \
(apply/consider/skip), confidence (0-1), strengths, gaps, critical_gaps (gaps \
that would actually block success in the role), transferable_experience (gaps \
the candidate's other experience likely covers), interview_risk (low/medium/high \
— how likely an interview surfaces a disqualifying issue), summary (2-3 \
sentences), and recommended_cv (which CV variant to use, if the candidate has \
more than one context — otherwise null).
"""


def _candidate_summary_text(profile: CandidateProfile) -> tuple[str, str, str, str]:
    roles = ", ".join(profile.roles) or "none listed"
    skills = ", ".join(skill.name for skill in profile.skills) or "none listed"
    experience = (
        "; ".join(f"{entry.title} at {entry.company}" for entry in profile.experience)
        or "none listed"
    )
    achievements = ", ".join(profile.achievements) or "none listed"
    return roles, skills, experience, achievements


class LlmReranker:
    def __init__(self, llm_provider: LLMProvider, budget: DailyCallBudget):
        self._llm_provider = llm_provider
        self._budget = budget

    async def assess(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        breakdown: ScoreBreakdown,
        strengths: list[MatchReason],
        gaps: list[MatchGap],
    ) -> LlmAssessment | None:
        """Returns None (not an error) when the daily call budget is exhausted —
        callers degrade to deterministic-only, same as every other optional AI
        layer in this app."""
        if not await self._budget.try_consume():
            return None

        roles, skills, experience, achievements = _candidate_summary_text(profile)
        prompt = _PROMPT.format(
            job_title=job.title,
            company=job.company,
            job_description=job.description,
            candidate_roles=roles,
            candidate_skills=skills,
            candidate_experience=experience,
            candidate_achievements=achievements,
            skills=breakdown.skills,
            role=breakdown.role,
            experience=breakdown.experience,
            semantic_fit=breakdown.semantic_fit,
            salary=breakdown.salary,
            location=breakdown.location,
            transferable_skills=breakdown.transferable_skills,
            preferences=breakdown.preferences,
            strengths=", ".join(reason.label for reason in strengths) or "none",
            gaps=", ".join(gap.label for gap in gaps) or "none",
        )

        result = await self._llm_provider.structured_completion(prompt, _LlmVerdict)
        verdict = result.data
        return LlmAssessment(
            overall_fit=verdict.overall_fit,
            recommendation=Recommendation(verdict.recommendation),
            confidence=verdict.confidence,
            strengths=verdict.strengths,
            gaps=verdict.gaps,
            critical_gaps=verdict.critical_gaps,
            transferable_experience=verdict.transferable_experience,
            interview_risk=verdict.interview_risk,
            summary=verdict.summary,
            recommended_cv=verdict.recommended_cv,
            model_label=result.model_label,
        )
