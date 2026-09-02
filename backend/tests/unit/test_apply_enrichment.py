"""Folding a review back into the match it reviewed. The rules that matter: the
model's opinion is already in the score, so it doesn't also get to set the label;
a downgraded gap stops being a gap; and provenance says an LLM was involved.
"""

from app.domain.matching.enrichment import EnrichedResult, apply_enrichment
from app.domain.matching.models import (
    JobMatch,
    MatchGap,
    Recommendation,
    ScoreBreakdown,
)
from app.domain.matching.provenance import (
    AnalysisLevel,
    MatchEngine,
    MatchProvenance,
)


def _match(score: float = 70.0) -> JobMatch:
    return JobMatch(
        id="m1",
        user_id="u1",
        canonical_job_id="c1",
        eligible=True,
        requirement_match=50.0,
        practical_fit=score,
        breakdown=ScoreBreakdown(50, 80, 100, 70, 100, 100, 60, 100),
        gaps=[MatchGap(label="Kafka", critical=True), MatchGap(label="Terraform", critical=False)],
        recommendation=Recommendation.CONSIDER,
        confidence=0.6,
        risks=["No compensation stated."],
        provenance=MatchProvenance(
            engine=MatchEngine.HYBRID, analysis_level=AnalysisLevel.STANDARD
        ),
    )


def _result(**overrides) -> EnrichedResult:
    defaults = {
        "score": 78.0,
        "confidence": 0.76,
        "recommendation": Recommendation.APPLY,
        "summary": "Strong fit; the Kafka gap is real but learnable.",
        "confirmed_gaps": ["Kafka"],
        "downgraded_gaps": [],
        "transferable_strengths": ["Python"],
        "risks": ["On-call rotation is not described."],
        "model_label": "Gemini (gemini-2.0-flash)",
    }
    return EnrichedResult(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_the_score_band_sets_the_label_not_the_models_own_recommendation() -> None:
    # The model's opinion already moved the score, and agreeing with it was
    # rewarded there; letting it set the label too would count it twice.
    enriched = apply_enrichment(_match(), _result(score=60.0, recommendation=Recommendation.APPLY))

    assert enriched.practical_fit == 60.0
    assert enriched.recommendation is Recommendation.CONSIDER
    # Its own view is still visible, next to the score.
    assert enriched.llm_assessment is not None
    assert enriched.llm_assessment.recommendation is Recommendation.APPLY


def test_a_downgraded_gap_stops_being_shown_as_a_gap() -> None:
    enriched = apply_enrichment(_match(), _result(downgraded_gaps=["Terraform"]))

    assert [gap.label for gap in enriched.gaps] == ["Kafka"]


def test_a_confirmed_gap_is_marked_critical() -> None:
    match = _match()
    match = JobMatch(**{**vars(match), "gaps": [MatchGap(label="Kafka", critical=False)]})

    enriched = apply_enrichment(match, _result(confirmed_gaps=["Kafka"]))

    assert enriched.gaps[0].critical is True


def test_provenance_records_that_an_llm_reviewed_this() -> None:
    enriched = apply_enrichment(_match(), _result())

    assert enriched.provenance is not None
    assert enriched.provenance.engine is MatchEngine.LLM_ENRICHED
    assert enriched.provenance.analysis_level is AnalysisLevel.FULL
    assert enriched.provenance.match_model == "Gemini (gemini-2.0-flash)"
    assert enriched.provenance.versions.match_prompt == "enrich-1"
    assert enriched.provenance.fallback_reason is None


def test_the_review_replaces_the_risks_it_was_asked_about() -> None:
    enriched = apply_enrichment(_match(), _result())

    assert enriched.risks == ["On-call rotation is not described."]
    assert enriched.confidence == 0.76


def test_a_review_with_no_risks_keeps_the_ones_the_pipeline_found() -> None:
    # Silence is not a statement that nothing is unknown.
    enriched = apply_enrichment(_match(), _result(risks=[]))

    assert enriched.risks == ["No compensation stated."]
