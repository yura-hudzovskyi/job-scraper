"""A complete, explainable match without a generative model — see
docs/ai-pipeline-v3.md (E4, G2).

This is what the pipeline produces when no LLM is available, and it has to be a
real answer rather than a degraded one: matched skills, genuine gaps, the
unknowns behind them, a score, and a confidence that says how much of that was
actually established. Embeddings and rerankers can order vacancies; they cannot
say *why*. The ontology and the requirement framing can, which is why the
explanation here is templated from evidence instead of generated prose.

Two things it is careful to keep apart:

- **A gap and an unknown.** Only a stated requirement the candidate demonstrably
  lacks is a gap. A mention the posting never framed is a risk to show, never a
  missing skill (SkillOutcome.UNKNOWN).
- **A score and its certainty.** The score is comparable; the confidence says how
  much evidence stood behind it. A posting with no extracted requirements can
  still score — it just can't claim to have checked anything, and the two numbers
  say so separately rather than one of them lying.
"""

from dataclasses import dataclass

from app.domain.candidates.experience import total_years
from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.categories import CategoryDecision
from app.domain.jobs.models import NormalizedJob, RequirementType
from app.domain.matching.calibration import CALIBRATION_VERSION
from app.domain.matching.models import MatchGap, MatchReason, Recommendation
from app.domain.matching.provenance import AnalysisLevel
from app.domain.matching.skill_matching import SkillAssessment, SkillFinding, SkillOutcome

SCORER_VERSION = "hybrid-2"

_REQUIRED_FRAMINGS = (RequirementType.REQUIRED_EXPLICIT, RequirementType.REQUIRED_INFERRED)

# Starting weights from the plan (G2). They sum to 1 and are hypotheses until the
# labelled set in phase 9 replaces them.
WEIGHT_REQUIRED_SKILLS = 0.30
WEIGHT_EXPERIENCE = 0.20
WEIGHT_RELEVANCE = 0.20
WEIGHT_RESPONSIBILITIES = 0.15
WEIGHT_SENIORITY = 0.10
WEIGHT_PREFERENCES = 0.05

# What each seniority label implies in years. Deliberately bands, not points:
# titles are approximate and a hard cut would fail a candidate over one month.
_SENIORITY_YEARS: dict[str, float] = {
    "intern": 0.0,
    "trainee": 0.0,
    "junior": 1.0,
    "middle": 3.0,
    "mid": 3.0,
    "senior": 5.0,
    "lead": 7.0,
    "principal": 8.0,
    "architect": 8.0,
}

_MAX_LISTED = 5

# Recommendation bands. They live here, with the engine that produces the score,
# so the service, the enricher and the evaluation harness all read one definition
# instead of three copies that can drift apart.
APPLY_BAND = 75.0
CONSIDER_BAND = 55.0

# A vacancy from another profession can score well on everything except the thing
# that matters — "you will talk to CTOs about Python and AWS" matches every
# keyword and requires none of them. Neither signal alone is enough to say so;
# both being low is. Validated against all-MiniLM-L6-v2, where genuine mismatches
# cluster ~15-30 and legitimate pivots ~40-60, so re-check these if the embedding
# model changes.
DOMAIN_MISMATCH_ROLE_CEILING = 35.0
DOMAIN_MISMATCH_SEMANTIC_CEILING = 35.0

# Embeddings are fooled by exactly the vacancy the plan warns about: a sales
# posting that lists Python, React and AWS reads as similar to a backend CV
# because the words are all there. The category is the signal that isn't fooled,
# so a confidently different profession forces the same verdict the similarity
# gate would have, and an adjacent one discounts role/domain fit rather than
# ruling anything out.
SOFT_CATEGORY_PENALTY = 0.85


def recommend(score: float, domain_mismatch: bool = False) -> Recommendation:
    """The actionable label. A domain mismatch forces SKIP without touching the
    score: capping the number would also punish legitimate career pivots, which
    are supposed to score decently."""
    if domain_mismatch:
        return Recommendation.SKIP
    if score >= APPLY_BAND:
        return Recommendation.APPLY
    if score >= CONSIDER_BAND:
        return Recommendation.CONSIDER
    return Recommendation.SKIP


@dataclass(frozen=True)
class MatchDimensions:
    """The comparable facets of one match, each 0-100. The same shape an
    LLM-enriched result reports, so the UI never branches on which engine ran."""

    required_skills: float
    relevant_experience: float
    seniority: float
    role_domain_fit: float
    responsibilities: float
    preferences: float


@dataclass(frozen=True)
class HybridResult:
    score: float
    confidence: float
    analysis_level: AnalysisLevel
    dimensions: MatchDimensions
    findings: list[SkillFinding]
    strengths: list[MatchReason]
    gaps: list[MatchGap]
    # Things this result could not establish — shown as unknowns, never as gaps.
    risks: list[str]
    # Both the role and the semantic signal say "different profession". Kept as a
    # flag rather than folded into the score — see recommend().
    domain_mismatch: bool = False
    scorer_version: str = SCORER_VERSION
    calibration_version: str = CALIBRATION_VERSION


def _seniority_score(job: NormalizedJob, years: float | None) -> float:
    """How well the candidate's time in the field fits what the posting asks for.
    Unknown on either side scores neutral rather than penalising: a missing label
    is not evidence of a mismatch."""
    if job.seniority is None or years is None:
        return 100.0
    expected = _SENIORITY_YEARS.get(job.seniority.strip().lower())
    if expected is None or expected <= 0:
        return 100.0
    if years >= expected:
        return 100.0
    # Linear down to zero: half the expected experience scores 50.
    return max(0.0, years / expected * 100)


def _experience_score(job: NormalizedJob, years: float | None) -> float:
    required = job.required_experience_years
    if required is None or required <= 0:
        return 100.0
    if years is None:
        # The posting asks for a number and the CV's dates are unreadable. Scoring
        # this as a miss would punish a formatting problem; the confidence drop
        # and the risk line carry the uncertainty instead.
        return 100.0
    return max(0.0, min(1.0, years / required)) * 100


def _relevance_score(relevance: float | None, semantic_fit: float) -> float:
    """The reranker's calibrated relevance when it ran. When it didn't, semantic
    similarity stands in — the weight is not silently dropped, because a missing
    reranker shouldn't make every job look better or worse than it is."""
    return relevance * 100 if relevance is not None else semantic_fit


def _confidence(
    findings: list[SkillFinding],
    reranked: bool,
    dated_experience: bool,
    skills_extracted_by: str | None,
) -> float:
    """How much of this result rests on established fact. Every term is something
    that either happened or didn't — no free-floating constant."""
    if not findings:
        # Nothing was checked against. That is a real answer, at low confidence.
        return 0.25

    framed = [
        finding
        for finding in findings
        if finding.requirement
        not in (RequirementType.UNKNOWN, RequirementType.CONTEXT)
    ]
    framing_ratio = len(framed) / len(findings)
    evidence_ratio = sum(1 for finding in findings if finding.evidence) / len(findings)

    confidence = 0.35
    confidence += 0.25 * framing_ratio
    confidence += 0.10 * evidence_ratio
    if reranked:
        confidence += 0.15
    if dated_experience:
        confidence += 0.10
    if skills_extracted_by and not skills_extracted_by.startswith("rules"):
        # A model read the posting; the rules extractor finds less and says so.
        confidence += 0.05
    return round(min(1.0, confidence), 2)


def _risks(
    job: NormalizedJob,
    findings: list[SkillFinding],
    years: float | None,
) -> list[str]:
    """Only things that are genuinely unknown. Every line here is a reason the
    score could move once the missing information exists."""
    risks: list[str] = []
    if not findings:
        risks.append("No requirements could be extracted from this posting — nothing was checked.")
    elif not any(finding.requirement in _REQUIRED_FRAMINGS for finding in findings):
        risks.append(
            "This posting names technologies but asks for none of them — the match rests on "
            "everything except its requirements."
        )

    unknown = [finding.name for finding in findings if finding.outcome is SkillOutcome.UNKNOWN]
    if unknown:
        risks.append(
            "The posting mentions "
            + ", ".join(unknown[:_MAX_LISTED])
            + " without saying whether they are required."
        )

    partial = [
        finding.name for finding in findings if finding.outcome is SkillOutcome.PARTIAL
    ]
    if partial:
        risks.append(
            "Adjacent experience only for " + ", ".join(partial[:_MAX_LISTED]) + "."
        )

    if years is None and job.required_experience_years:
        risks.append(
            "This posting asks for "
            f"{job.required_experience_years:g}+ years, but the CV's dates could not be read."
        )
    if job.salary is None:
        risks.append("No compensation stated.")
    return risks


def _strengths(findings: list[SkillFinding]) -> list[MatchReason]:
    """Templated from evidence, not generated: each line points at the
    requirement it came from and what satisfied it."""
    strengths = []
    for finding in findings:
        if not finding.satisfied:
            continue
        if finding.outcome is SkillOutcome.MATCHED_EQUIVALENT:
            detail = f"{finding.name} is covered by your {finding.matched_by or 'related'} experience"
        elif finding.matched_by and finding.matched_by != finding.name:
            detail = f"{finding.name} matches your {finding.matched_by}"
        else:
            detail = f"{finding.name} appears in the job and in your profile"
        if finding.evidence:
            detail = f"{detail} — the posting says: {finding.evidence}"
        strengths.append(MatchReason(label=finding.name, detail=detail))
    return strengths[:_MAX_LISTED]


def _gaps(findings: list[SkillFinding]) -> list[MatchGap]:
    return [
        MatchGap(
            label=finding.name,
            critical=finding.requirement
            in (RequirementType.REQUIRED_EXPLICIT, RequirementType.REQUIRED_INFERRED),
        )
        for finding in findings
        if finding.outcome is SkillOutcome.MISSING
    ][:_MAX_LISTED]


class HybridMatchEngine:
    """Pure computation over already-gathered signals: the caller runs the
    embedding, rerank and extraction steps, this turns them into a result. No IO
    of its own, which is what makes the scoring rules testable in isolation."""

    def evaluate(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
        skills: SkillAssessment,
        semantic_fit: float,
        role_fit: float,
        salary_score: float,
        location_score: float,
        rerank_relevance: float | None = None,
        category: CategoryDecision = CategoryDecision.PASS,
    ) -> HybridResult:
        computed_years = total_years(profile.experience)
        years = computed_years if computed_years is not None else profile.experience_years

        # A posting that names technologies without requiring any of them (the
        # keyword-trap vacancy: "you'll talk to CTOs about Python and AWS") has
        # nothing to cover, and crediting full requirement coverage for that reads
        # as a perfect match. The dimension is dropped instead of faked, and its
        # weight is shared out — same reasoning as the pre-v3 scorer's
        # skills_available guard, applied to framing rather than presence.
        assessed_requirements = any(
            finding.requirement in _REQUIRED_FRAMINGS for finding in skills.findings
        )

        dimensions = MatchDimensions(
            required_skills=skills.required_coverage * 100,
            relevant_experience=_experience_score(job, years),
            seniority=_seniority_score(job, years),
            role_domain_fit=_relevance_score(rerank_relevance, semantic_fit)
            * (SOFT_CATEGORY_PENALTY if category is CategoryDecision.SOFT_MISMATCH else 1.0),
            responsibilities=semantic_fit,
            # One dimension for "does this suit what they asked for": stack
            # preference, pay and place are the same question to a candidate.
            preferences=(skills.preferences_score + salary_score + location_score) / 3,
        )

        weighted = (
            dimensions.relevant_experience * WEIGHT_EXPERIENCE
            + dimensions.role_domain_fit * WEIGHT_RELEVANCE
            + dimensions.responsibilities * WEIGHT_RESPONSIBILITIES
            + dimensions.seniority * WEIGHT_SENIORITY
            + dimensions.preferences * WEIGHT_PREFERENCES
        )
        if assessed_requirements:
            score = weighted + dimensions.required_skills * WEIGHT_REQUIRED_SKILLS
        else:
            # Rescale what is left so the score still spans 0-100 rather than
            # topping out at 70 for every unassessable posting.
            score = weighted / (1 - WEIGHT_REQUIRED_SKILLS)

        domain_mismatch = category is CategoryDecision.HARD_MISMATCH or (
            role_fit < DOMAIN_MISMATCH_ROLE_CEILING
            and semantic_fit < DOMAIN_MISMATCH_SEMANTIC_CEILING
        )

        findings = skills.findings
        confidence = _confidence(
            findings,
            reranked=rerank_relevance is not None,
            dated_experience=computed_years is not None,
            skills_extracted_by=job.skills_extracted_by,
        )
        return HybridResult(
            score=round(score, 1),
            confidence=confidence,
            analysis_level=(
                AnalysisLevel.STANDARD if assessed_requirements else AnalysisLevel.LIMITED
            ),
            dimensions=dimensions,
            findings=findings,
            strengths=_strengths(findings),
            gaps=(
                [MatchGap(label="role/domain mismatch", critical=True), *_gaps(findings)]
                if domain_mismatch
                else _gaps(findings)
            ),
            risks=_risks(job, findings, computed_years),
            domain_mismatch=domain_mismatch,
        )
