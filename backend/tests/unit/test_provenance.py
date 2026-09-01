"""Provenance has to survive a round trip through JSONB unchanged — including
versions recorded under an older pipeline, which must never be re-read as today's.
"""

from datetime import UTC, datetime

from app.domain.matching.provenance import (
    AnalysisLevel,
    FallbackReason,
    MatchEngine,
    MatchProvenance,
    PipelineVersions,
    provenance_from_payload,
    provenance_payload,
)
from app.domain.versioning import DocumentVersion


def _provenance(**overrides: object) -> MatchProvenance:
    defaults: dict[str, object] = {
        "engine": MatchEngine.DETERMINISTIC,
        "analysis_level": AnalysisLevel.FULL,
        "profile": DocumentVersion(version=7, content_hash="cafe1234cafe1234"),
        "job": DocumentVersion(version=3, content_hash="beef5678beef5678"),
        "embedding_model": "all-MiniLM-L6-v2",
        "cross_encoder_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "skills_model": "Groq (llama-3.3-70b-versatile)",
        "match_model": "Gemini (gemini-2.0-flash)",
        "fallback_reason": None,
        "generated_at": datetime(2026, 9, 1, 12, 30, tzinfo=UTC),
    }
    return MatchProvenance(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_provenance_round_trips_through_a_json_payload() -> None:
    provenance = _provenance()

    assert provenance_from_payload(provenance_payload(provenance)) == provenance


def test_a_fallback_reason_round_trips() -> None:
    provenance = _provenance(
        analysis_level=AnalysisLevel.STANDARD,
        match_model=None,
        fallback_reason=FallbackReason.LLM_BUDGET_EXHAUSTED,
    )

    restored = provenance_from_payload(provenance_payload(provenance))

    assert restored is not None
    assert restored.fallback_reason is FallbackReason.LLM_BUDGET_EXHAUSTED
    assert restored.match_model is None


def test_an_old_row_keeps_the_versions_it_was_scored_under() -> None:
    # The whole point of storing versions: bumping SCORER_VERSION must not
    # retroactively relabel results produced by the previous scorer.
    stored = provenance_payload(_provenance(versions=PipelineVersions(scorer="0", match_prompt="0")))

    restored = provenance_from_payload(stored)

    assert restored is not None
    assert restored.versions.scorer == "0"
    assert restored.versions.match_prompt == "0"


def test_no_provenance_reads_back_as_none() -> None:
    assert provenance_from_payload(None) is None
