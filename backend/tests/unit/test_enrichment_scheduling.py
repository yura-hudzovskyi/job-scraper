"""Ordering decides where a scarce daily budget goes, so these pin the ordering
itself: boundary cases and disagreements before comfortable middling matches, and
nothing spent twice on the same job.
"""

from app.domain.matching.models import (
    JobMatch,
    LlmAssessment,
    Recommendation,
    ScoreBreakdown,
)
from app.domain.matching.scheduling import (
    EnrichmentReason,
    is_eligible,
    rank_for_enrichment,
    score_candidate,
)


def _match(
    job_id: str,
    score: float,
    *,
    confidence: float | None = 0.8,
    skills: float = 80.0,
    semantic: float = 80.0,
    recommendation: Recommendation = Recommendation.CONSIDER,
    eligible: bool = True,
    assessed: bool = False,
) -> JobMatch:
    return JobMatch(
        id=f"m-{job_id}",
        user_id="u1",
        canonical_job_id=job_id,
        eligible=eligible,
        requirement_match=score,
        practical_fit=score,
        breakdown=ScoreBreakdown(
            skills=skills,
            role=80,
            experience=80,
            semantic_fit=semantic,
            salary=100,
            location=100,
            transferable_skills=80,
            preferences=100,
        ),
        recommendation=recommendation,
        confidence=confidence,
        llm_assessment=(
            LlmAssessment(
                overall_fit=80,
                recommendation=Recommendation.APPLY,
                confidence=0.8,
                strengths=[],
                gaps=[],
                critical_gaps=[],
                transferable_experience=[],
                interview_risk="low",
                summary="",
                recommended_cv=None,
                model_label="fake",
            )
            if assessed
            else None
        ),
    )


def test_a_match_on_the_apply_boundary_outranks_a_comfortable_one() -> None:
    boundary = _match("a", 74.0)
    comfortable = _match("b", 92.0)

    ranked = rank_for_enrichment([comfortable, boundary], limit=2)

    assert [candidate.match.canonical_job_id for candidate in ranked] == ["a", "b"]
    assert ranked[0].reason is EnrichmentReason.DECISION_BOUNDARY


def test_disagreeing_signals_raise_priority() -> None:
    # Requirement coverage says yes, similarity says no. One of them is wrong and
    # a reader can tell which.
    agreeing = _match("a", 65.0, skills=80, semantic=80)
    disagreeing = _match("b", 65.0, skills=95, semantic=20)

    ranked = rank_for_enrichment([agreeing, disagreeing], limit=2)

    assert ranked[0].match.canonical_job_id == "b"
    assert ranked[0].priority > score_candidate(agreeing).priority


def test_low_confidence_raises_priority() -> None:
    confident = _match("a", 65.0, confidence=0.95)
    uncertain = _match("b", 65.0, confidence=0.3)

    ranked = rank_for_enrichment([confident, uncertain], limit=2)

    assert ranked[0].match.canonical_job_id == "b"


def test_an_already_analysed_match_is_not_a_candidate() -> None:
    # Re-analysing spends budget to produce the answer already stored.
    assert is_eligible(_match("a", 80.0, assessed=True)) is False
    assert rank_for_enrichment([_match("a", 80.0, assessed=True)], limit=5) == []


def test_skipped_and_ineligible_matches_are_not_candidates() -> None:
    assert is_eligible(_match("a", 20.0, recommendation=Recommendation.SKIP)) is False
    assert is_eligible(_match("b", 80.0, eligible=False)) is False


def test_the_limit_is_respected_and_ties_are_deterministic() -> None:
    matches = [_match(str(index), 65.0) for index in range(5)]

    first = rank_for_enrichment(matches, limit=3)
    second = rank_for_enrichment(list(reversed(matches)), limit=3)

    assert len(first) == 3
    assert [candidate.match.canonical_job_id for candidate in first] == [
        candidate.match.canonical_job_id for candidate in second
    ]


def test_a_match_without_recorded_confidence_is_treated_as_middling() -> None:
    # Pre-hybrid rows have no confidence; assuming certainty would push them to
    # the back of every queue forever.
    unknown = score_candidate(_match("a", 65.0, confidence=None))
    certain = score_candidate(_match("b", 65.0, confidence=1.0))

    assert unknown.priority > certain.priority
