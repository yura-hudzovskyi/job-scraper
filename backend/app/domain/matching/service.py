"""Orchestrates the matching pipeline: filters -> deterministic score -> semantic
score -> skill match -> explanation. See docs/matching-engine.md.

LLM reranking and "should I apply?" are Phase 4 (docs/roadmap.md) — evaluate() stops
at the deterministic+semantic+skill pipeline, which is already fully explainable on
its own.

This is the only entry point other modules should call — it composes
HardFilterService, DeterministicScorer, SemanticScorer and SkillMatcher, none of
which should be called directly from services/ or the API layer.
"""

from dataclasses import replace

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.filters import HardFilterService
from app.domain.matching.models import (
    JobMatch,
    MatchGap,
    MatchReason,
    Recommendation,
    ScoreBreakdown,
)
from app.domain.matching.scoring import DeterministicScore, DeterministicScorer, SemanticScorer
from app.domain.matching.skill_matching import SkillAssessment, SkillMatcher

_APPLY_THRESHOLD = 75.0
_CONSIDER_THRESHOLD = 55.0
_MAX_LISTED_REASONS = 5


class MatchingService:
    def __init__(
        self,
        hard_filters: HardFilterService,
        deterministic_scorer: DeterministicScorer,
        semantic_scorer: SemanticScorer,
        skill_matcher: SkillMatcher,
    ):
        self._hard_filters = hard_filters
        self._deterministic_scorer = deterministic_scorer
        self._semantic_scorer = semantic_scorer
        self._skill_matcher = skill_matcher

    async def evaluate(
        self,
        canonical_job_id: str,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
    ) -> JobMatch:
        """Run the full pipeline for a single job and return an explainable JobMatch."""
        filter_result = self._hard_filters.evaluate(job, preferences)
        if not filter_result.eligible:
            return JobMatch(
                id="",
                user_id=profile.user_id,
                canonical_job_id=canonical_job_id,
                eligible=False,
                requirement_match=0.0,
                practical_fit=0.0,
                breakdown=ScoreBreakdown(0, 0, 0, 0, 0, 0, 0, 0),
                gaps=[MatchGap(label=reason, critical=True) for reason in filter_result.reasons],
                recommendation=Recommendation.SKIP,
            )

        role, experience, salary, location = self._deterministic_scorer.score(
            job, profile, preferences
        )
        skill_assessment = await self._skill_matcher.assess(
            job_skills=job.skills,
            candidate_skills=[skill.name for skill in profile.skills],
            preferred_stack=preferences.preferred_stack,
            acceptable_stack=preferences.acceptable_stack,
        )
        semantic_fit = await self._semantic_scorer.similarity(job, profile) * 100

        deterministic = DeterministicScore(
            skills=skill_assessment.skills_score,
            role=role,
            experience=experience,
            transferable_skills=skill_assessment.transferable_score,
            salary=salary,
            location=location,
            preferences=skill_assessment.preferences_score,
        )

        practical_fit = self._deterministic_scorer.overall(deterministic, semantic_fit)
        # "Requirement match" is the literal fit — transferable-skill credit doesn't count.
        requirement_match = self._deterministic_scorer.overall(
            replace(deterministic, transferable_skills=0.0), semantic_fit
        )

        breakdown = ScoreBreakdown(
            skills=deterministic.skills,
            role=deterministic.role,
            experience=deterministic.experience,
            semantic_fit=semantic_fit,
            salary=deterministic.salary,
            location=deterministic.location,
            transferable_skills=deterministic.transferable_skills,
            preferences=deterministic.preferences,
        )

        strengths, gaps = self._explain(skill_assessment)

        return JobMatch(
            id="",
            user_id=profile.user_id,
            canonical_job_id=canonical_job_id,
            eligible=True,
            requirement_match=requirement_match,
            practical_fit=practical_fit,
            breakdown=breakdown,
            strengths=strengths,
            gaps=gaps,
            recommendation=self._recommend(practical_fit),
        )

    def _explain(
        self, skill_assessment: SkillAssessment
    ) -> tuple[list[MatchReason], list[MatchGap]]:
        strengths = [
            MatchReason(label=skill, detail=f"{skill} appears in the job and in your profile")
            for skill in skill_assessment.strengths[:_MAX_LISTED_REASONS]
        ]
        gaps = [
            MatchGap(label=skill, critical=is_required)
            for skill, is_required in skill_assessment.gaps[:_MAX_LISTED_REASONS]
        ]
        return strengths, gaps

    def _recommend(self, practical_fit: float) -> Recommendation:
        if practical_fit >= _APPLY_THRESHOLD:
            return Recommendation.APPLY
        if practical_fit >= _CONSIDER_THRESHOLD:
            return Recommendation.CONSIDER
        return Recommendation.SKIP

    async def rerank_shortlist(self, matches: list[JobMatch]) -> list[JobMatch]:
        """Send the top-N deterministic+semantic matches to the LLM for reranking and
        gap analysis. Phase 4 — see docs/roadmap.md."""
        raise NotImplementedError

    async def should_i_apply(self, match: JobMatch) -> str:
        """Structured, explainable apply/skip recommendation for a single match.
        Phase 4 — see docs/roadmap.md."""
        raise NotImplementedError
