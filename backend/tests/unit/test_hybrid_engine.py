"""The hybrid engine has to produce a *usable* result with no LLM anywhere, and
be honest about what it couldn't establish. These pin the distinctions that make
that true: gap versus unknown, score versus confidence, reranked versus not.
"""

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    ExperienceEntry,
    SkillLevel,
    UserPreference,
)
from app.domain.jobs.models import (
    EmploymentType,
    JobLocation,
    NormalizedJob,
    RequirementType,
    SalaryRange,
)
from app.domain.matching.hybrid import HybridMatchEngine
from app.domain.matching.provenance import AnalysisLevel
from app.domain.matching.skill_matching import SkillAssessment, SkillFinding, SkillOutcome

ENGINE = HybridMatchEngine()


def _job(**overrides: object) -> NormalizedJob:
    defaults: dict[str, object] = {
        "source": "dou",
        "external_id": "1",
        "url": "https://example.com/1",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "description": "Own the payments API.",
        "employment_type": EmploymentType.FULL_TIME,
        "location": JobLocation(remote=True),
        "salary": SalaryRange(min=4000, max=6000, currency="USD"),
        "seniority": "senior",
        "required_experience_years": 5.0,
        "skills_extracted_by": "Groq (llama-3.3-70b-versatile)",
    }
    return NormalizedJob(**{**defaults, **overrides})  # type: ignore[arg-type]


def _profile(years: float = 6.0, dated: bool = True) -> CandidateProfile:
    experience = (
        [
            ExperienceEntry(
                company="Acme",
                title="Engineer",
                start_date="2020-01",
                end_date="2026-01",
                description="APIs",
                skills=["Python"],
            )
        ]
        if dated
        else [
            ExperienceEntry(
                company="Acme",
                title="Engineer",
                start_date="a while ago",
                end_date=None,
                description="APIs",
                skills=["Python"],
            )
        ]
    )
    return CandidateProfile(
        id="p1",
        user_id="u1",
        experience_years=years,
        roles=["Backend Engineer"],
        skills=[CandidateSkill(name="Python", level=SkillLevel.STRONG)],
        experience=experience,
    )


def _finding(name: str, outcome: SkillOutcome, requirement: RequirementType, evidence=None):
    return SkillFinding(
        name=name, requirement=requirement, outcome=outcome, evidence=evidence
    )


def _skills(findings: list[SkillFinding], preferences_score: float = 100.0) -> SkillAssessment:
    satisfied = [finding for finding in findings if finding.satisfied]
    return SkillAssessment(
        skills_score=len(satisfied) / len(findings) * 100 if findings else 100.0,
        transferable_score=100.0,
        preferences_score=preferences_score,
        strengths=[finding.name for finding in satisfied],
        gaps=[(finding.name, True) for finding in findings if finding.is_gap],
        findings=findings,
    )


def _evaluate(findings, **overrides):
    kwargs: dict[str, object] = {
        "job": _job(),
        "profile": _profile(),
        "preferences": UserPreference(user_id="u1", desired_salary_usd=None),
        "skills": _skills(findings),
        "semantic_fit": 70.0,
        "role_fit": 80.0,
        "salary_score": 100.0,
        "location_score": 100.0,
    }
    kwargs.update(overrides)
    return ENGINE.evaluate(**kwargs)  # type: ignore[arg-type]


def test_a_strong_match_scores_high_with_evidence_backed_strengths() -> None:
    result = _evaluate(
        [
            _finding(
                "Python",
                SkillOutcome.MATCHED,
                RequirementType.REQUIRED_EXPLICIT,
                evidence="5+ years of Python required.",
            )
        ]
    )

    assert result.score > 80
    assert result.dimensions.required_skills == 100.0
    assert result.strengths[0].label == "Python"
    # The explanation points at the posting's own words rather than paraphrasing.
    assert "5+ years of Python required." in result.strengths[0].detail


def test_a_missing_requirement_is_a_gap_and_drags_the_score_down() -> None:
    result = _evaluate(
        [
            _finding("Python", SkillOutcome.MATCHED, RequirementType.REQUIRED_EXPLICIT),
            _finding("Kafka", SkillOutcome.MISSING, RequirementType.REQUIRED_EXPLICIT),
        ]
    )

    assert result.dimensions.required_skills == 50.0
    assert [gap.label for gap in result.gaps] == ["Kafka"]
    assert result.gaps[0].critical is True


def test_an_unknown_mention_is_a_risk_not_a_gap() -> None:
    # The single rule the plan is most emphatic about.
    result = _evaluate(
        [
            _finding("Python", SkillOutcome.MATCHED, RequirementType.REQUIRED_EXPLICIT),
            _finding("Rust", SkillOutcome.UNKNOWN, RequirementType.UNKNOWN),
        ]
    )

    assert result.gaps == []
    assert any("without saying whether they are required" in risk for risk in result.risks)


def test_adjacent_experience_is_reported_as_a_risk_not_a_match() -> None:
    result = _evaluate(
        [_finding("Kubernetes", SkillOutcome.PARTIAL, RequirementType.REQUIRED_EXPLICIT)]
    )

    assert result.strengths == []
    assert any("Adjacent experience only" in risk for risk in result.risks)


def test_a_posting_with_no_requirements_still_scores_but_says_so() -> None:
    result = _evaluate([])

    assert result.analysis_level is AnalysisLevel.LIMITED
    assert result.confidence <= 0.3
    assert any("nothing was checked" in risk for risk in result.risks)


def test_confidence_rises_with_evidence_framing_and_a_rerank() -> None:
    bare = _evaluate([_finding("Python", SkillOutcome.MATCHED, RequirementType.UNKNOWN)])
    evidenced = _evaluate(
        [
            _finding(
                "Python",
                SkillOutcome.MATCHED,
                RequirementType.REQUIRED_EXPLICIT,
                evidence="Python required.",
            )
        ],
        rerank_relevance=0.8,
    )

    assert evidenced.confidence > bare.confidence
    assert evidenced.confidence <= 1.0


def test_a_rerank_relevance_replaces_the_semantic_stand_in() -> None:
    findings = [_finding("Python", SkillOutcome.MATCHED, RequirementType.REQUIRED_EXPLICIT)]

    without = _evaluate(findings)
    with_rerank = _evaluate(findings, rerank_relevance=0.95)

    assert without.dimensions.role_domain_fit == 70.0  # semantic fit stands in
    assert with_rerank.dimensions.role_domain_fit == 95.0
    assert with_rerank.score > without.score


def test_unreadable_cv_dates_lower_confidence_rather_than_the_score() -> None:
    findings = [_finding("Python", SkillOutcome.MATCHED, RequirementType.REQUIRED_EXPLICIT)]

    dated = _evaluate(findings)
    undated = _evaluate(findings, profile=_profile(dated=False))

    assert undated.confidence < dated.confidence
    # A formatting problem in a CV is not evidence of missing experience.
    assert undated.dimensions.relevant_experience == 100.0
    assert any("dates could not be read" in risk for risk in undated.risks)


def test_less_experience_than_asked_for_lowers_the_experience_dimension() -> None:
    findings = [_finding("Python", SkillOutcome.MATCHED, RequirementType.REQUIRED_EXPLICIT)]

    result = _evaluate(
        findings,
        job=_job(required_experience_years=10.0),
        profile=_profile(),
    )

    assert result.dimensions.relevant_experience < 100.0


def test_a_junior_candidate_against_a_senior_posting_scores_seniority_down() -> None:
    findings = [_finding("Python", SkillOutcome.MATCHED, RequirementType.REQUIRED_EXPLICIT)]

    junior = CandidateProfile(
        id="p2",
        user_id="u1",
        experience_years=1.0,
        roles=["Backend Engineer"],
        skills=[CandidateSkill(name="Python", level=SkillLevel.AWARE)],
        experience=[
            ExperienceEntry(
                company="Acme",
                title="Junior Engineer",
                start_date="2025-01",
                end_date="2026-01",
                description="APIs",
                skills=["Python"],
            )
        ],
    )

    result = _evaluate(findings, profile=junior)

    assert result.dimensions.seniority < 50.0


def test_an_unlabelled_seniority_does_not_penalise_anyone() -> None:
    findings = [_finding("Python", SkillOutcome.MATCHED, RequirementType.REQUIRED_EXPLICIT)]

    result = _evaluate(findings, job=_job(seniority=None))

    assert result.dimensions.seniority == 100.0
