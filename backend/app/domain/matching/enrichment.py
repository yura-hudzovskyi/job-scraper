"""The LLM as a reviewer of a finished analysis, not as the thing that produces
it — see docs/ai-pipeline-v3.md (F1, F2, G2).

The retired `AiMatcher` asked a model to score a job from scratch. This asks a
narrower and much more answerable question: here is what the deterministic
pipeline found — these requirements, these gaps, these unknowns, this score —
what does it have wrong? A model is genuinely better than a formula at "is this
apparent gap actually a blocker" and "does this experience transfer"; it is not
better at arithmetic, and it never gets to own the number.

Two guardrails make its answer usable:

- **Claims are checked against the inputs.** A confirmed gap has to be one of the
  gaps it was shown; a transferable strength has to name a skill that appears in
  the posting or the CV. Anything else is dropped, because a claim about a skill
  nobody mentioned is the exact hallucination this design is meant to survive.
- **The score stays arithmetic.** The model moves dimensions up or down and
  offers a recommendation; the weighted sum happens here, so an enriched score is
  comparable with a hybrid one instead of being whatever number a model felt like
  emitting.
"""

import logging
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.candidates.models import CandidateProfile
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.hybrid import (
    WEIGHT_EXPERIENCE,
    WEIGHT_PREFERENCES,
    WEIGHT_RELEVANCE,
    WEIGHT_REQUIRED_SKILLS,
    WEIGHT_RESPONSIBILITIES,
    WEIGHT_SENIORITY,
    MatchDimensions,
    recommend,
)
from app.domain.matching.models import (
    JobMatch,
    LlmAssessment,
    MatchGap,
    Recommendation,
)
from app.domain.matching.provenance import AnalysisLevel, MatchEngine
from app.domain.skills.normalizer import dedupe_key
from app.integrations.ai.llm.base import LLMProvider

logger = logging.getLogger(__name__)

PROMPT_VERSION = "enrich-1"

# How far one judgment may move a dimension. Enough to change a recommendation at
# the boundary, not enough to overturn an analysis built on extracted evidence.
_JUDGMENT_STEP = 15.0

# G2's split: the hybrid analysis stays the base, the model's review adjusts it,
# and agreeing with its own recommendation is worth something on its own.
WEIGHT_HYBRID_BASE = 0.60
WEIGHT_LLM_DIMENSIONS = 0.30
WEIGHT_RECOMMENDATION_CONSISTENCY = 0.10

_DIMENSION_WEIGHTS = {
    "required_skills": WEIGHT_REQUIRED_SKILLS,
    "relevant_experience": WEIGHT_EXPERIENCE,
    "role_domain_fit": WEIGHT_RELEVANCE,
    "responsibilities": WEIGHT_RESPONSIBILITIES,
    "seniority": WEIGHT_SENIORITY,
    "preferences": WEIGHT_PREFERENCES,
}


class _DimensionJudgment(BaseModel):
    dimension: Literal[
        "required_skills",
        "relevant_experience",
        "seniority",
        "role_domain_fit",
        "responsibilities",
        "preferences",
    ]
    verdict: Literal["higher", "as_scored", "lower"]
    reason: str


class _EnrichmentVerdict(BaseModel):
    dimension_judgments: list[_DimensionJudgment] = Field(default_factory=list)
    confirmed_gaps: list[str] = Field(default_factory=list)
    downgraded_gaps: list[str] = Field(default_factory=list)
    transferable_strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendation: Literal["apply", "consider", "skip"]
    confidence: float = Field(ge=0, le=1)
    summary: str


@dataclass(frozen=True)
class EnrichedResult:
    score: float
    confidence: float
    recommendation: Recommendation
    summary: str
    confirmed_gaps: list[str]
    downgraded_gaps: list[str]
    transferable_strengths: list[str]
    risks: list[str]
    model_label: str
    prompt_version: str = PROMPT_VERSION
    # Claims that named something absent from both documents, dropped rather than
    # shown. Counted so a model that hallucinates often is visible in the logs.
    rejected_claims: int = 0


_PROMPT = """A deterministic pipeline has already analysed this match. Hard \
eligibility constraints were checked separately — assume the job is eligible.

Your job is to review that analysis, not to redo it. You may not invent a skill \
that appears in neither document.

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

What the pipeline found:
- Score {score:.0f}/100 (confidence {confidence:.2f}), recommendation: {recommendation}
- Requirements it checked: {requirements}
- Gaps it found: {gaps}
- Things it could not establish: {risks}
- Dimension scores (0-100): required skills {required_skills:.0f}, relevant \
experience {relevant_experience:.0f}, seniority {seniority:.0f}, role/domain fit \
{role_domain_fit:.0f}, responsibilities {responsibilities:.0f}, preferences \
{preferences:.0f}

Answer with:
- dimension_judgments: for any dimension the pipeline got wrong, whether it \
should be higher or lower and why. Leave out the ones it got right.
- confirmed_gaps: which of the listed gaps really would block this candidate. \
Use the exact names from the gap list.
- downgraded_gaps: which listed gaps their other experience likely covers. Same names.
- transferable_strengths: experience that counts for this role even though the \
pipeline didn't credit it. Name skills that appear above.
- risks: what could still go wrong in an interview, in plain language.
- recommendation: apply, consider or skip.
- confidence: 0-1, how sure you are.
- summary: 2-3 sentences for the candidate.
"""


def _band(score: float) -> Recommendation:
    """One definition of the bands, shared with the engine that produced the
    score (app/domain/matching/hybrid.py)."""
    return recommend(score)


def _consistency(llm: Recommendation, scored: Recommendation) -> float:
    """How much the model's own recommendation agrees with where the score lands.
    Adjacent bands are a partial agreement; opposite ends are not."""
    if llm is scored:
        return 100.0
    order = [Recommendation.SKIP, Recommendation.CONSIDER, Recommendation.APPLY]
    return 60.0 if abs(order.index(llm) - order.index(scored)) == 1 else 0.0


def _adjusted_dimensions(
    dimensions: MatchDimensions, judgments: list[_DimensionJudgment]
) -> float:
    """The dimensions again, nudged by the model, re-summed with the same weights.
    Same arithmetic as the hybrid score — only the inputs moved."""
    values = {
        "required_skills": dimensions.required_skills,
        "relevant_experience": dimensions.relevant_experience,
        "seniority": dimensions.seniority,
        "role_domain_fit": dimensions.role_domain_fit,
        "responsibilities": dimensions.responsibilities,
        "preferences": dimensions.preferences,
    }
    for judgment in judgments:
        if judgment.verdict == "as_scored":
            continue
        step = _JUDGMENT_STEP if judgment.verdict == "higher" else -_JUDGMENT_STEP
        values[judgment.dimension] = max(0.0, min(100.0, values[judgment.dimension] + step))
    return sum(values[name] * weight for name, weight in _DIMENSION_WEIGHTS.items())


def _known_names(job: NormalizedJob, profile: CandidateProfile) -> set[str]:
    return {dedupe_key(skill.name) for skill in job.skills} | {
        dedupe_key(skill.name) for skill in profile.skills
    }


def _kept(claims: list[str], allowed: set[str]) -> tuple[list[str], int]:
    kept = [claim for claim in claims if dedupe_key(claim) in allowed]
    return kept, len(claims) - len(kept)


class LlmMatchEnricher:
    def __init__(self, llm_provider: LLMProvider):
        self._llm_provider = llm_provider

    async def enrich(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        dimensions: MatchDimensions,
        score: float,
        confidence: float,
        recommendation: Recommendation,
        gaps: list[str],
        risks: list[str],
    ) -> EnrichedResult:
        """Raises whatever the router raises (NoCapacity included) — the caller
        decides whether that means "keep the hybrid result" or "come back
        later"."""
        prompt = _PROMPT.format(
            job_title=job.title,
            company=job.company,
            job_description=job.description,
            candidate_roles=", ".join(profile.roles) or "none listed",
            candidate_skills=", ".join(skill.name for skill in profile.skills) or "none listed",
            candidate_experience="; ".join(
                f"{entry.title} at {entry.company}" for entry in profile.experience
            )
            or "none listed",
            score=score,
            confidence=confidence,
            recommendation=recommendation.value,
            requirements=", ".join(skill.name for skill in job.skills) or "none extracted",
            gaps=", ".join(gaps) or "none",
            risks="; ".join(risks) or "none",
            required_skills=dimensions.required_skills,
            relevant_experience=dimensions.relevant_experience,
            seniority=dimensions.seniority,
            role_domain_fit=dimensions.role_domain_fit,
            responsibilities=dimensions.responsibilities,
            preferences=dimensions.preferences,
        )
        result = await self._llm_provider.structured_completion(prompt, _EnrichmentVerdict)
        verdict = result.data

        gap_keys = {dedupe_key(gap) for gap in gaps}
        confirmed, rejected_confirmed = _kept(verdict.confirmed_gaps, gap_keys)
        downgraded, rejected_downgraded = _kept(verdict.downgraded_gaps, gap_keys)
        strengths, rejected_strengths = _kept(
            verdict.transferable_strengths, _known_names(job, profile)
        )
        rejected = rejected_confirmed + rejected_downgraded + rejected_strengths
        if rejected:
            logger.info(
                "dropped %d claim(s) from %s that named nothing in either document",
                rejected,
                result.model_label,
            )

        llm_recommendation = Recommendation(verdict.recommendation)
        enriched_score = (
            score * WEIGHT_HYBRID_BASE
            + _adjusted_dimensions(dimensions, verdict.dimension_judgments)
            * WEIGHT_LLM_DIMENSIONS
            + _consistency(llm_recommendation, _band(score)) * WEIGHT_RECOMMENDATION_CONSISTENCY
        )
        return EnrichedResult(
            score=round(enriched_score, 1),
            # Two methods agreeing is worth more than either alone, but the
            # model's own certainty is self-reported — it gets the smaller share.
            confidence=round(min(1.0, confidence * 0.6 + verdict.confidence * 0.4), 2),
            recommendation=llm_recommendation,
            summary=verdict.summary,
            confirmed_gaps=confirmed,
            downgraded_gaps=downgraded,
            transferable_strengths=strengths,
            risks=verdict.risks,
            model_label=result.model_label,
            rejected_claims=rejected,
        )


def apply_enrichment(match: JobMatch, result: EnrichedResult) -> JobMatch:
    """Fold a review back into the match it reviewed.

    The recommendation comes from the *score band*, not from the model's own
    label: the model's opinion already moved the score (and agreeing with it was
    rewarded), so letting it also set the label directly would count it twice and
    break comparability with unenriched results. Its label is still stored, in
    `llm_assessment`, where a user can see the two side by side.

    A gap the model downgraded stops being shown as a gap — that judgement is the
    whole reason to ask — while a confirmed one is marked critical.
    """
    downgraded = {dedupe_key(name) for name in result.downgraded_gaps}
    confirmed = {dedupe_key(name) for name in result.confirmed_gaps}
    gaps = [
        MatchGap(label=gap.label, critical=gap.critical or dedupe_key(gap.label) in confirmed)
        for gap in match.gaps
        if dedupe_key(gap.label) not in downgraded
    ]

    provenance = match.provenance
    if provenance is not None:
        provenance = replace(
            provenance,
            engine=MatchEngine.LLM_ENRICHED,
            analysis_level=AnalysisLevel.FULL,
            match_model=result.model_label,
            fallback_reason=None,
            versions=replace(provenance.versions, match_prompt=result.prompt_version),
        )

    return replace(
        match,
        practical_fit=result.score,
        recommendation=_band(result.score),
        confidence=result.confidence,
        gaps=gaps,
        risks=result.risks or match.risks,
        llm_assessment=LlmAssessment(
            overall_fit=result.score,
            recommendation=result.recommendation,
            confidence=result.confidence,
            strengths=result.transferable_strengths,
            gaps=result.confirmed_gaps,
            critical_gaps=result.confirmed_gaps,
            transferable_experience=result.transferable_strengths,
            interview_risk="high" if len(result.risks) > 2 else "medium" if result.risks else "low",
            summary=result.summary,
            recommended_cv=None,
            model_label=result.model_label,
        ),
        provenance=provenance,
    )
