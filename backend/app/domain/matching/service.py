"""Orchestrates the matching pipeline: filters -> deterministic score -> semantic
score -> explanation. See docs/matching-engine.md.

LLM reranking and "should I apply?" are Phase 4 (docs/roadmap.md) — evaluate() stops
at the deterministic+semantic pipeline, which is already fully explainable on its own.

This is the only entry point other modules should call — it composes
HardFilterService, DeterministicScorer and SemanticScorer, none of which should be
called directly from services/ or the API layer.
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
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer

_APPLY_THRESHOLD = 75.0
_CONSIDER_THRESHOLD = 55.0
_MAX_LISTED_REASONS = 5


class MatchingService:
    def __init__(
        self,
        hard_filters: HardFilterService,
        deterministic_scorer: DeterministicScorer,
        semantic_scorer: SemanticScorer,
    ):
        self._hard_filters = hard_filters
        self._deterministic_scorer = deterministic_scorer
        self._semantic_scorer = semantic_scorer

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

        deterministic = self._deterministic_scorer.score(job, profile, preferences)
        semantic_fit = await self._semantic_scorer.similarity(job, profile) * 100

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

        strengths, gaps = self._explain(job, profile)

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
        self, job: NormalizedJob, profile: CandidateProfile
    ) -> tuple[list[MatchReason], list[MatchGap]]:
        exact, missing = self._deterministic_scorer.skill_gap_analysis(job, profile)

        strengths = [
            MatchReason(label=skill, detail=f"{skill} appears in the job and in your profile")
            for skill in exact[:_MAX_LISTED_REASONS]
        ]
        # Criticality isn't determinable from text-mined mentions alone — real
        # required-vs-nice-to-have extraction is Phase 4 (LLM requirement extraction).
        gaps = [
            MatchGap(label=skill, critical=False) for skill in missing[:_MAX_LISTED_REASONS]
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
