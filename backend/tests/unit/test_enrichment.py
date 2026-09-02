"""Enrichment is the one place a model's opinion enters a score, so the tests are
about the guardrails: it can move dimensions but not own the number, and a claim
about something nobody mentioned never reaches the user.
"""

import pytest

from app.domain.candidates.models import CandidateProfile, CandidateSkill, SkillLevel
from app.domain.jobs.models import (
    EmploymentType,
    JobLocation,
    NormalizedJob,
    NormalizedJobSkill,
    RequirementType,
)
from app.domain.matching.enrichment import PROMPT_VERSION, LlmMatchEnricher
from app.domain.matching.hybrid import MatchDimensions
from app.domain.matching.models import Recommendation
from app.integrations.ai.llm.base import LLMResult


class _FakeProvider:
    def __init__(self, payload: dict):
        self._payload = payload
        self.prompts: list[str] = []

    async def structured_completion(self, prompt, schema):
        self.prompts.append(prompt)
        return LLMResult(data=schema(**self._payload), model_label="Groq (fake)")


def _job() -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="1",
        url="https://example.com/1",
        title="Senior Backend Engineer",
        company="Acme",
        description="Own the payments API.",
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=True),
        salary=None,
        seniority="senior",
        required_experience_years=5.0,
        skills=[
            NormalizedJobSkill(name="Python", requirement=RequirementType.REQUIRED_EXPLICIT),
            NormalizedJobSkill(name="Kafka", requirement=RequirementType.REQUIRED_EXPLICIT),
        ],
    )


def _profile() -> CandidateProfile:
    return CandidateProfile(
        id="p1",
        user_id="u1",
        experience_years=6.0,
        roles=["Backend Engineer"],
        skills=[CandidateSkill(name="Python", level=SkillLevel.STRONG)],
    )


_DIMENSIONS = MatchDimensions(
    required_skills=50.0,
    relevant_experience=100.0,
    seniority=100.0,
    role_domain_fit=70.0,
    responsibilities=70.0,
    preferences=100.0,
)

_BASE_PAYLOAD = {
    "dimension_judgments": [],
    "confirmed_gaps": [],
    "downgraded_gaps": [],
    "transferable_strengths": [],
    "risks": [],
    "recommendation": "consider",
    "confidence": 0.8,
    "summary": "Reasonable fit with one real gap.",
}


async def _enrich(payload_overrides: dict, score: float = 70.0, gaps: list[str] | None = None):
    provider = _FakeProvider({**_BASE_PAYLOAD, **payload_overrides})
    enricher = LlmMatchEnricher(provider)  # type: ignore[arg-type]
    result = await enricher.enrich(
        job=_job(),
        profile=_profile(),
        dimensions=_DIMENSIONS,
        score=score,
        confidence=0.6,
        recommendation=Recommendation.CONSIDER,
        gaps=gaps if gaps is not None else ["Kafka"],
        risks=["No compensation stated."],
    )
    return result, provider


@pytest.mark.asyncio
async def test_the_pipeline_result_is_what_the_model_is_asked_to_review() -> None:
    _, provider = await _enrich({})

    prompt = provider.prompts[0]
    assert "review that analysis, not to redo it" in prompt
    assert "Gaps it found: Kafka" in prompt
    assert "Things it could not establish: No compensation stated." in prompt


@pytest.mark.asyncio
async def test_the_model_moves_dimensions_but_does_not_own_the_score() -> None:
    agreeing, _ = await _enrich({})
    raising, _ = await _enrich(
        {
            "dimension_judgments": [
                {
                    "dimension": "required_skills",
                    "verdict": "higher",
                    "reason": "Kafka is trivial to pick up here",
                }
            ]
        }
    )

    assert raising.score > agreeing.score
    # The hybrid analysis is still most of the answer — one judgment can't turn a
    # 70 into a 95.
    assert raising.score - agreeing.score < 10
    assert PROMPT_VERSION == raising.prompt_version


@pytest.mark.asyncio
async def test_a_claim_about_a_skill_nobody_mentioned_is_dropped() -> None:
    result, _ = await _enrich(
        {
            "confirmed_gaps": ["Kafka", "Erlang"],
            "transferable_strengths": ["Python", "Haskell"],
        }
    )

    assert result.confirmed_gaps == ["Kafka"]
    assert result.transferable_strengths == ["Python"]
    assert result.rejected_claims == 2


@pytest.mark.asyncio
async def test_a_gap_the_model_did_not_see_cannot_be_confirmed() -> None:
    # Confirmed and downgraded gaps must come from the list it was shown.
    result, _ = await _enrich({"downgraded_gaps": ["Kubernetes"]}, gaps=["Kafka"])

    assert result.downgraded_gaps == []
    assert result.rejected_claims == 1


@pytest.mark.asyncio
async def test_names_are_matched_the_way_the_ontology_matches_them() -> None:
    result, _ = await _enrich({"confirmed_gaps": ["kafka"]})

    assert result.confirmed_gaps == ["kafka"]
    assert result.rejected_claims == 0


@pytest.mark.asyncio
async def test_agreeing_with_the_score_band_is_worth_more_than_contradicting_it() -> None:
    agreeing, _ = await _enrich({"recommendation": "consider"}, score=70.0)
    contradicting, _ = await _enrich({"recommendation": "skip"}, score=70.0)

    assert agreeing.score > contradicting.score


@pytest.mark.asyncio
async def test_confidence_blends_both_methods_with_the_model_weighted_less() -> None:
    # The model's certainty is self-reported; the pipeline's is built from what it
    # actually established.
    result, _ = await _enrich({"confidence": 1.0})

    assert result.confidence == pytest.approx(0.6 * 0.6 + 0.4 * 1.0, abs=0.01)


@pytest.mark.asyncio
async def test_the_verdict_carries_the_model_that_produced_it() -> None:
    result, _ = await _enrich({})

    assert result.model_label == "Groq (fake)"
    assert result.recommendation is Recommendation.CONSIDER
    assert result.summary == "Reasonable fit with one real gap."
