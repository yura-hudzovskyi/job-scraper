"""Primary matching path — one structured LLM call replaces the multi-stage
deterministic pipeline (scoring.py + skill_matching.py + role_matching.py) as the
thing that actually decides a job's fit. Hard filters (filters.py) still run first
in MatchingService.evaluate — non-negotiable candidate-configured constraints
(blacklisted company, blocked stack, salary floor, location) are never left to an
LLM to reinterpret or hallucinate past.

The deterministic pipeline becomes the *fallback*: used only when no LLM is
configured, or when this call fails or returns something we can't trust (timeout,
malformed output, provider unreachable) — same "degrade gracefully, don't crash
the pipeline" policy every other optional AI layer in this app already follows
(see LlmReranker, JobSkillExtractionService). Returning None (never raising) on
failure is what makes that fallback possible.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.models import (
    JobMatch,
    MatchGap,
    MatchReason,
    Recommendation,
    ScoreBreakdown,
)
from app.integrations.ai.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_MAX_LISTED_REASONS = 5


class _AiBreakdown(BaseModel):
    skills: float = Field(ge=0, le=100)
    role: float = Field(ge=0, le=100)
    experience: float = Field(ge=0, le=100)
    semantic_fit: float = Field(ge=0, le=100)
    salary: float = Field(ge=0, le=100)
    location: float = Field(ge=0, le=100)
    transferable_skills: float = Field(ge=0, le=100)
    preferences: float = Field(ge=0, le=100)


class _AiReason(BaseModel):
    label: str
    detail: str


class _AiGap(BaseModel):
    label: str
    critical: bool


class _AiVerdict(BaseModel):
    requirement_match: float = Field(ge=0, le=100)
    practical_fit: float = Field(ge=0, le=100)
    breakdown: _AiBreakdown
    strengths: list[_AiReason] = Field(default_factory=list)
    gaps: list[_AiGap] = Field(default_factory=list)
    recommendation: Literal["apply", "consider", "skip"]


_PROMPT = """You are scoring how well a candidate fits a job posting. Hard \
eligibility constraints (blacklists, salary floor, location, blocked stack) were \
already checked separately — assume this job is eligible and focus entirely on fit.

Job title: {job_title}
Company: {company}
Seniority: {seniority}
Required experience: {required_experience}
Salary: {salary}
Location: {location}
Job description:
---
{job_description}
---

Candidate:
- Roles: {candidate_roles}
- Skills: {candidate_skills}
- Experience: {candidate_experience}
- Achievements: {candidate_achievements}
- Domains: {candidate_domains}
- Total experience: {candidate_experience_years} years

Candidate's stated preferences:
- Desired salary: {desired_salary}
- Preferred locations: {preferred_locations}
- Preferred stack: {preferred_stack}
- Acceptable stack (willing but not preferred): {acceptable_stack}
- Max required experience they'll consider: {max_required_experience}

Score two overall numbers, 0-100:
- requirement_match: how well the candidate LITERALLY satisfies what's listed — no \
credit for skills the posting doesn't ask for or for transferable/adjacent \
experience standing in for a missing requirement.
- practical_fit: how well the candidate would actually perform, crediting \
transferable/adjacent experience (e.g. Django experience counts meaningfully \
toward a FastAPI role) and overall trajectory, not just a literal checklist match.

Also score these 8 components, 0-100 each, that explain practical_fit:
- skills: required/nice-to-have technical skills the candidate has.
- role: how well the job title matches the candidate's roles/preferred roles.
- experience: years of experience vs. what's required.
- semantic_fit: overall thematic closeness between the posting and the candidate's \
background beyond a literal skills/role match.
- salary: how well the posting's salary meets the candidate's desired salary \
(100 if not specified or not comparable).
- location: how well the posting's location/remote policy matches the candidate's \
preferred locations (100 if not specified).
- transferable_skills: how much of what's missing is plausibly covered by adjacent \
experience the candidate already has.
- preferences: how well the stack matches the candidate's preferred/acceptable stack \
(100 if the candidate listed no stack preferences).

List up to {max_reasons} strengths (skill/experience the job and candidate both \
have, with a one-line detail) and up to {max_reasons} gaps (label + whether it's a \
critical/required gap vs a nice-to-have).

Give a recommendation: "apply" (strong match, worth prioritizing), "consider" \
(decent match, worth a look), or "skip" (weak match, not worth pursuing).
"""


def _profile_summary(profile: CandidateProfile) -> tuple[str, str, str, str, str]:
    roles = ", ".join(profile.roles) or "none listed"
    skills = ", ".join(skill.name for skill in profile.skills) or "none listed"
    experience = (
        "; ".join(f"{entry.title} at {entry.company}: {entry.description}" for entry in profile.experience)
        or "none listed"
    )
    achievements = ", ".join(profile.achievements) or "none listed"
    domains = ", ".join(profile.domains) or "none listed"
    return roles, skills, experience, achievements, domains


class AiMatcher:
    def __init__(self, llm_provider: LLMProvider):
        self._llm_provider = llm_provider

    async def assess(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
    ) -> JobMatch | None:
        """Returns None (never raises) on any failure — timeout, provider down,
        malformed output — so MatchingService can fall back to the deterministic
        pipeline instead of losing the score entirely."""
        roles, skills, experience, achievements, domains = _profile_summary(profile)
        prompt = _PROMPT.format(
            job_title=job.title,
            company=job.company,
            seniority=job.seniority or "not specified",
            required_experience=(
                f"{job.required_experience_years}+ years"
                if job.required_experience_years
                else "not specified"
            ),
            salary=(
                f"{job.salary.min}-{job.salary.max} {job.salary.currency}"
                if job.salary
                else "not specified"
            ),
            location=(
                "remote" if job.location.remote else ", ".join(job.location.countries + job.location.cities) or "not specified"
            ),
            job_description=job.description,
            candidate_roles=roles,
            candidate_skills=skills,
            candidate_experience=experience,
            candidate_achievements=achievements,
            candidate_domains=domains,
            candidate_experience_years=profile.experience_years,
            desired_salary=(
                f"${preferences.desired_salary_usd} USD" if preferences.desired_salary_usd else "not specified"
            ),
            preferred_locations=", ".join(preferences.locations) or "no preference",
            preferred_stack=", ".join(preferences.preferred_stack) or "none listed",
            acceptable_stack=", ".join(preferences.acceptable_stack) or "none listed",
            max_required_experience=preferences.max_required_experience or "no cap",
            max_reasons=_MAX_LISTED_REASONS,
        )

        try:
            result = await self._llm_provider.structured_completion(prompt, _AiVerdict)
        except Exception:
            logger.warning(
                "AI matching failed for job %r — falling back to deterministic scoring",
                job.title,
                exc_info=True,
            )
            return None

        verdict = result.data
        return JobMatch(
            id="",
            user_id="",
            canonical_job_id="",
            eligible=True,
            requirement_match=verdict.requirement_match,
            practical_fit=verdict.practical_fit,
            breakdown=ScoreBreakdown(**verdict.breakdown.model_dump()),
            strengths=[
                MatchReason(label=reason.label, detail=reason.detail)
                for reason in verdict.strengths[:_MAX_LISTED_REASONS]
            ],
            gaps=[
                MatchGap(label=gap.label, critical=gap.critical)
                for gap in verdict.gaps[:_MAX_LISTED_REASONS]
            ],
            recommendation=Recommendation(verdict.recommendation),
            scored_by=f"AI ({result.model_label})",
        )
