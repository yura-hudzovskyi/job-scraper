"""Orchestrates the matching pipeline: filters -> deterministic score -> semantic
score -> skill match -> explanation -> (optional) LLM overlay. See
docs/matching-engine.md.

evaluate() runs cheap, non-negotiable hard filters first (company/stack/salary/
location constraints the candidate explicitly configured — never left to an LLM to
reinterpret), then always runs the deterministic + semantic + skill pipeline
(_evaluate_deterministic below) — no LLM involved, already fully explainable on its
own — to produce the authoritative score. Nothing downstream ever overwrites
requirement_match/practical_fit once this has run.

should_i_apply() is a separate, optional Phase 4 step callers run afterward for
matches worth a closer look (see its own docstring for the CONSIDER+APPLY gating and
volume controls) — it layers a qualitative LLM verdict (llm_assessment) on top of
the already-authoritative deterministic score, it never replaces it.
rerank_shortlist() (batch reranking) is still deferred — no shortlist view or digest
batching exists to feed it.

This is the only entry point other modules should call — it composes
HardFilterService, DeterministicScorer, SemanticScorer, SkillMatcher, RoleMatcher and
(optionally) LlmReranker, none of which should be called directly from services/ or
the API layer.
"""

import asyncio
from dataclasses import dataclass, replace

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.categories import candidate_categories, decide
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.filters import HardFilterService
from app.domain.matching.hybrid import HybridMatchEngine, HybridResult, recommend
from app.domain.matching.llm_reranker import LlmReranker
from app.domain.matching.models import (
    JobMatch,
    MatchGap,
    MatchReason,
    Recommendation,
    ScoreBreakdown,
)
from app.domain.matching.provenance import (
    AnalysisLevel,
    FallbackReason,
    MatchEngine,
    MatchProvenance,
    PipelineModels,
    PipelineVersions,
    now,
)
from app.domain.matching.role_matching import RoleMatcher
from app.domain.matching.scoring import DeterministicScore, DeterministicScorer, SemanticScorer
from app.domain.matching.skill_matching import SkillAssessment, SkillMatcher
from app.domain.versioning import DocumentVersion
from app.integrations.ai.routing.router import NoCapacity

_MAX_LISTED_REASONS = 5


def _profile_version(profile: CandidateProfile) -> DocumentVersion | None:
    """None for a profile saved before content hashes existed — better than
    inventing an identity for a snapshot that can't actually be identified."""
    if profile.content_hash is None:
        return None
    return DocumentVersion(version=profile.version, content_hash=profile.content_hash)


@dataclass(frozen=True)
class MatchingThresholds:
    """Recommendation-band thresholds for match *quality*. Deliberately kept
    separate from NotificationPolicyConfig (app/domain/notifications/policy.py),
    which answers a different question — delivery *urgency* — even though both
    are "threshold numbers applied to a JobMatch." Merging them would recouple
    two concerns that should be free to evolve independently.
    """

    apply: float = 75.0
    consider: float = 55.0
    # Below these, a job that only skill-matches superficially (skills_available
    # is True but role/semantic both say "different profession") gets its
    # Recommendation forced to SKIP regardless of practical_fit — see the
    # domain-mismatch gate in evaluate(). Validated against real embeddings
    # (all-MiniLM-L6-v2): genuine mismatches cluster ~15-30, legitimate
    # adjacent/pivot roles cluster ~40-60 — re-validate if the embedding
    # provider ever changes, since the margin is model-specific.
    domain_mismatch_role_ceiling: float = 35.0
    domain_mismatch_semantic_ceiling: float = 35.0


class MatchingService:
    def __init__(
        self,
        hard_filters: HardFilterService,
        deterministic_scorer: DeterministicScorer,
        semantic_scorer: SemanticScorer,
        skill_matcher: SkillMatcher,
        role_matcher: RoleMatcher,
        thresholds: MatchingThresholds | None = None,
        llm_reranker: LlmReranker | None = None,
        models: PipelineModels | None = None,
        hybrid_engine: HybridMatchEngine | None = None,
    ):
        self._hard_filters = hard_filters
        self._deterministic_scorer = deterministic_scorer
        self._semantic_scorer = semantic_scorer
        self._skill_matcher = skill_matcher
        self._role_matcher = role_matcher
        self._thresholds = thresholds or MatchingThresholds()
        self._llm_reranker = llm_reranker
        # Recorded, not used: which models this instance was built with, so every
        # result it produces can say so (see provenance.py).
        self._models = models or PipelineModels()
        # When present, the hybrid engine owns the scoring rules and this class
        # keeps doing what it always did: gather the signals, apply the gates,
        # persistably shape the result. None means the pre-v3 weighted scorer,
        # which stays until MATCHING_PIPELINE_V3 is on everywhere.
        self._hybrid_engine = hybrid_engine

    async def evaluate(
        self,
        canonical_job_id: str,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
        job_version: DocumentVersion | None = None,
        rerank_relevance: float | None = None,
        rerank_model: str | None = None,
    ) -> JobMatch:
        """Run the full pipeline for a single job and return an explainable JobMatch.
        Hard filters gate eligibility first; the deterministic pipeline then decides
        fit — see the module docstring. `job_version` identifies the revision of the
        posting being scored (app/domain/versioning.py); the caller looks it up, this
        service only records it."""
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
                # Nothing was analyzed — a hard filter answered before scoring ran.
                provenance=self._provenance(AnalysisLevel.LIMITED, profile, job, job_version),
            )

        return await self._evaluate_deterministic(
            canonical_job_id, job, profile, preferences, job_version, rerank_relevance, rerank_model
        )

    async def _evaluate_deterministic(
        self,
        canonical_job_id: str,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
        job_version: DocumentVersion | None = None,
        rerank_relevance: float | None = None,
        rerank_model: str | None = None,
    ) -> JobMatch:
        """The authoritative scoring path: deterministic + semantic + skill, no LLM
        involved (see module docstring). `rerank_relevance`, when the retrieval
        pass has produced one, replaces semantic similarity as the role/domain
        signal — a model that read both documents beats comparing two vectors."""
        experience, salary, location = self._deterministic_scorer.score(job, profile, preferences)
        skill_assessment, semantic_similarity, role = await asyncio.gather(
            self._skill_matcher.assess(
                job_skills=job.skills,
                candidate_skills=[skill.name for skill in profile.skills],
                preferred_stack=preferences.preferred_stack,
                acceptable_stack=preferences.acceptable_stack,
            ),
            self._semantic_scorer.similarity(job, profile),
            self._role_matcher.assess(job.title, preferences.preferred_roles, profile.roles),
        )
        semantic_fit = semantic_similarity * 100

        if self._hybrid_engine is not None:
            return self._from_hybrid(
                canonical_job_id,
                job,
                profile,
                job_version,
                self._hybrid_engine.evaluate(
                    job=job,
                    profile=profile,
                    preferences=preferences,
                    skills=skill_assessment,
                    semantic_fit=semantic_fit,
                    role_fit=role,
                    salary_score=salary,
                    location_score=location,
                    rerank_relevance=rerank_relevance,
                    # Keyword-heavy postings from another profession look similar
                    # to everything; their category doesn't.
                    category=decide(
                        job.category,
                        job.category_confidence,
                        candidate_categories([*preferences.preferred_roles, *profile.roles]),
                    ),
                ),
                role=role,
                salary=salary,
                location=location,
                transferable=skill_assessment.transferable_score,
                rerank_model=rerank_model,
            )

        deterministic = DeterministicScore(
            skills=skill_assessment.skills_score,
            role=role,
            experience=experience,
            transferable_skills=skill_assessment.transferable_score,
            salary=salary,
            location=location,
            preferences=skill_assessment.preferences_score,
        )

        # An empty job.skills means SkillMatcher had nothing to assess and returned
        # its fabricated "neutral" 100/100/100 (see SkillMatcher._NEUTRAL) — that's
        # not a real signal, so overall() must not credit it at full weight.
        skills_available = bool(job.skills)

        practical_fit = self._deterministic_scorer.overall(
            deterministic, semantic_fit, skills_available=skills_available
        )
        # "Requirement match" is the literal fit — transferable-skill credit doesn't count.
        requirement_match = self._deterministic_scorer.overall(
            replace(deterministic, transferable_skills=0.0),
            semantic_fit,
            skills_available=skills_available,
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

        # A job that only skill-matches superficially (skills_available=True) but
        # is a different profession entirely — role and semantic fit both say so —
        # never scores low enough on its own to guarantee SKIP (worst case floors
        # around 70, the CONSIDER band: see docs/matching-engine.md). A hard cap on
        # the *score* would also penalize legitimate career pivots (e.g. a
        # Backend->DevOps title mismatch is supposed to score decently on
        # transferable grounds), so this overrides the *recommendation* only —
        # the score stays honest, only the actionable label + a visible reason
        # change.
        domain_mismatch = (
            skills_available
            and role < self._thresholds.domain_mismatch_role_ceiling
            and semantic_fit < self._thresholds.domain_mismatch_semantic_ceiling
        )
        if domain_mismatch:
            recommendation = Recommendation.SKIP
            gaps = [MatchGap(label="role/domain mismatch", critical=True), *gaps]
        else:
            recommendation = self._recommend(practical_fit)

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
            recommendation=recommendation,
            provenance=self._provenance(
                # No extracted requirements means nothing was really checked —
                # saying so is the honest analysis level, not STANDARD.
                AnalysisLevel.STANDARD if skills_available else AnalysisLevel.LIMITED,
                profile,
                job,
                job_version,
            ),
        )

    def _from_hybrid(
        self,
        canonical_job_id: str,
        job: NormalizedJob,
        profile: CandidateProfile,
        job_version: DocumentVersion | None,
        result: HybridResult,
        role: float,
        salary: float,
        location: float,
        transferable: float,
        rerank_model: str | None = None,
    ) -> JobMatch:
        """Shape the engine's result into the JobMatch every consumer already
        reads. The breakdown keeps its field names — the UI, the notifications
        and the Telegram cards don't need to learn a new vocabulary for the same
        six facets."""
        provenance = replace(
            self._provenance(result.analysis_level, profile, job, job_version),
            engine=MatchEngine.HYBRID,
            rerank_model=rerank_model,
            versions=replace(
                PipelineVersions(),
                scorer=result.scorer_version,
                calibration=result.calibration_version,
            ),
        )
        return JobMatch(
            id="",
            user_id=profile.user_id,
            canonical_job_id=canonical_job_id,
            eligible=True,
            # The literal fit: how much of what the posting requires is covered.
            requirement_match=result.dimensions.required_skills,
            practical_fit=result.score,
            breakdown=ScoreBreakdown(
                skills=result.dimensions.required_skills,
                role=role,
                experience=result.dimensions.relevant_experience,
                semantic_fit=result.dimensions.role_domain_fit,
                salary=salary,
                location=location,
                transferable_skills=transferable,
                preferences=result.dimensions.preferences,
            ),
            strengths=result.strengths,
            # The engine already marked a domain mismatch and prepended its gap —
            # that rule belongs with the scoring rules, not here.
            gaps=result.gaps,
            recommendation=recommend(result.score, result.domain_mismatch),
            confidence=result.confidence,
            risks=result.risks,
            provenance=provenance,
        )

    def _provenance(
        self,
        analysis_level: AnalysisLevel,
        profile: CandidateProfile,
        job: NormalizedJob,
        job_version: DocumentVersion | None,
    ) -> MatchProvenance:
        """Everything the deterministic path knows about how it produced a result.
        should_i_apply() amends it when the LLM layer runs on top."""
        return MatchProvenance(
            engine=MatchEngine.DETERMINISTIC,
            analysis_level=analysis_level,
            profile=_profile_version(profile),
            job=job_version,
            embedding_model=self._models.embedding,
            cross_encoder_model=self._models.cross_encoder,
            skills_model=job.skills_extracted_by,
            generated_at=now(),
        )

    def _record_fallback(self, match: JobMatch, reason: FallbackReason) -> JobMatch:
        """The LLM layer didn't contribute — say why, so the UI can explain the
        result instead of silently showing one with no verdict attached."""
        if match.provenance is None:
            return match
        return replace(match, provenance=replace(match.provenance, fallback_reason=reason))

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
        if practical_fit >= self._thresholds.apply:
            return Recommendation.APPLY
        if practical_fit >= self._thresholds.consider:
            return Recommendation.CONSIDER
        return Recommendation.SKIP

    async def rerank_shortlist(self, matches: list[JobMatch]) -> list[JobMatch]:
        """Batch LLM rerank over a shortlist. Deferred — there's no shortlist view
        or digest batching to feed it yet (mirrors notifications' own not-yet-built
        digest batching); should_i_apply() below covers the per-match case that's
        actually wired up today."""
        raise NotImplementedError

    async def should_i_apply(
        self, job: NormalizedJob, profile: CandidateProfile, match: JobMatch
    ) -> JobMatch:
        """Layers the LLM's qualitative verdict onto an already-scored match — see
        LlmReranker and docs/matching-engine.md's Phase 4 section. Never called for
        Recommendation.SKIP matches: that's both where the "should I apply?" question
        isn't worth asking, and the primary volume control on top of LlmReranker's
        own capability budget (see app/integrations/ai/quota/budget.py) — a
        personal-scale free-tier key can't afford reranking every match regardless of
        quality. When the layer can't run (no LLM configured, SKIP-tier, or the
        daily budget is exhausted) the match comes back scored exactly as before,
        with only its provenance amended to say why — same "degrade gracefully"
        policy as every other optional AI layer here, but no longer silent about
        it."""
        if self._llm_reranker is None:
            return self._record_fallback(match, FallbackReason.NO_LLM_PROVIDER)
        if match.recommendation == Recommendation.SKIP:
            return self._record_fallback(match, FallbackReason.BELOW_LLM_THRESHOLD)

        try:
            assessment = await self._llm_reranker.assess(
                job, profile, match.breakdown, match.strengths, match.gaps
            )
        except NoCapacity:
            return self._record_fallback(match, FallbackReason.LLM_NO_CAPACITY)

        provenance = match.provenance
        if provenance is not None:
            provenance = replace(
                provenance,
                analysis_level=AnalysisLevel.FULL,
                match_model=assessment.model_label,
                fallback_reason=None,
            )
        return replace(match, llm_assessment=assessment, provenance=provenance)
