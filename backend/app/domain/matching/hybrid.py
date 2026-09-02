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
from app.domain.jobs.models import NormalizedJob, RequirementType
from app.domain.matching.calibration import CALIBRATION_VERSION
from app.domain.matching.models import MatchGap, MatchReason
from app.domain.matching.provenance import AnalysisLevel
from app.domain.matching.skill_matching import SkillAssessment, SkillFinding, SkillOutcome

SCORER_VERSION = "hybrid-1"

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
    ) -> HybridResult:
        computed_years = total_years(profile.experience)
        years = computed_years if computed_years is not None else profile.experience_years

        dimensions = MatchDimensions(
            required_skills=skills.required_coverage * 100,
            relevant_experience=_experience_score(job, years),
            seniority=_seniority_score(job, years),
            role_domain_fit=_relevance_score(rerank_relevance, semantic_fit),
            responsibilities=semantic_fit,
            # One dimension for "does this suit what they asked for": stack
            # preference, pay and place are the same question to a candidate.
            preferences=(skills.preferences_score + salary_score + location_score) / 3,
        )

        score = (
            dimensions.required_skills * WEIGHT_REQUIRED_SKILLS
            + dimensions.relevant_experience * WEIGHT_EXPERIENCE
            + dimensions.role_domain_fit * WEIGHT_RELEVANCE
            + dimensions.responsibilities * WEIGHT_RESPONSIBILITIES
            + dimensions.seniority * WEIGHT_SENIORITY
            + dimensions.preferences * WEIGHT_PREFERENCES
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
            analysis_level=AnalysisLevel.STANDARD if findings else AnalysisLevel.LIMITED,
            dimensions=dimensions,
            findings=findings,
            strengths=_strengths(findings),
            gaps=_gaps(findings),
            risks=_risks(job, findings, computed_years),
        )
