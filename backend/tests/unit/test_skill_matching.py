import pytest

from app.domain.jobs.models import NormalizedJobSkill
from app.domain.matching.skill_matching import SkillMatcher


class _FakeEmbeddingProvider:
    """Hand-crafted vectors so cosine similarity between specific skill names is
    fully predictable, without needing a real embedding model."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


@pytest.mark.asyncio
async def test_empty_job_skills_returns_neutral_defaults() -> None:
    matcher = SkillMatcher(_FakeEmbeddingProvider({}))
    result = await matcher.assess([], ["Django"], ["React"], [])
    assert result.skills_score == 100.0
    assert result.transferable_score == 100.0
    assert result.preferences_score == 100.0
    assert result.strengths == []
    assert result.gaps == []


@pytest.mark.asyncio
async def test_matching_skill_above_threshold_counts_as_strength() -> None:
    vectors = {"Django": [1.0, 0.0], "django": [1.0, 0.0]}
    matcher = SkillMatcher(_FakeEmbeddingProvider(vectors), match_threshold=0.75)

    result = await matcher.assess(
        job_skills=[NormalizedJobSkill(name="Django", required=True)],
        candidate_skills=["django"],
        preferred_stack=[],
        acceptable_stack=[],
    )

    assert result.skills_score == 100.0
    assert result.strengths == ["Django"]
    assert result.gaps == []


@pytest.mark.asyncio
async def test_moderately_related_skill_is_a_gap_with_partial_transfer_credit() -> None:
    # cos(Django, FastAPI) = 0.5 — related enough for partial credit, below the
    # 0.75 match threshold so it still counts as a gap, not an exact match.
    vectors = {"FastAPI": [1.0, 0.0], "Django": [0.5, 0.8660254]}
    matcher = SkillMatcher(_FakeEmbeddingProvider(vectors), match_threshold=0.75)

    result = await matcher.assess(
        job_skills=[NormalizedJobSkill(name="FastAPI", required=True)],
        candidate_skills=["Django"],
        preferred_stack=[],
        acceptable_stack=[],
    )

    assert result.skills_score == 0.0
    assert result.gaps == [("FastAPI", True)]
    assert result.transferable_score == pytest.approx(50.0, abs=1.0)


@pytest.mark.asyncio
async def test_unrelated_required_skill_is_a_critical_gap_with_no_transfer_credit() -> None:
    vectors = {"Rust": [0.0, 1.0], "Photoshop": [1.0, 0.0]}
    matcher = SkillMatcher(_FakeEmbeddingProvider(vectors))

    result = await matcher.assess(
        job_skills=[NormalizedJobSkill(name="Rust", required=True)],
        candidate_skills=["Photoshop"],
        preferred_stack=[],
        acceptable_stack=[],
    )

    assert result.gaps == [("Rust", True)]
    assert result.transferable_score == 0.0


@pytest.mark.asyncio
async def test_nice_to_have_gap_is_not_marked_required() -> None:
    vectors = {"Kubernetes": [0.0, 1.0], "Docker": [1.0, 0.0]}
    matcher = SkillMatcher(_FakeEmbeddingProvider(vectors))

    result = await matcher.assess(
        job_skills=[NormalizedJobSkill(name="Kubernetes", required=False)],
        candidate_skills=["Docker"],
        preferred_stack=[],
        acceptable_stack=[],
    )

    assert result.gaps == [("Kubernetes", False)]


@pytest.mark.asyncio
async def test_preferences_score_rewards_preferred_over_acceptable_stack() -> None:
    vectors = {
        "React": [1.0, 0.0],
        "Vue": [0.0, 1.0],
        "Angular": [0.5, 0.0],  # similar-ish to React but not an exact match target
    }
    matcher = SkillMatcher(_FakeEmbeddingProvider(vectors), match_threshold=0.75)

    preferred = await matcher.assess(
        job_skills=[NormalizedJobSkill(name="React", required=True)],
        candidate_skills=["React"],
        preferred_stack=["React"],
        acceptable_stack=[],
    )
    acceptable = await matcher.assess(
        job_skills=[NormalizedJobSkill(name="React", required=True)],
        candidate_skills=["React"],
        preferred_stack=["Vue"],
        acceptable_stack=["React"],
    )
    neither = await matcher.assess(
        job_skills=[NormalizedJobSkill(name="React", required=True)],
        candidate_skills=["React"],
        preferred_stack=["Vue"],
        acceptable_stack=[],
    )

    assert preferred.preferences_score == 100.0
    assert acceptable.preferences_score == 60.0
    assert neither.preferences_score == 0.0


@pytest.mark.asyncio
async def test_preferences_score_neutral_when_no_stack_preference_set() -> None:
    vectors = {"React": [1.0, 0.0]}
    matcher = SkillMatcher(_FakeEmbeddingProvider(vectors))

    result = await matcher.assess(
        job_skills=[NormalizedJobSkill(name="React", required=True)],
        candidate_skills=["React"],
        preferred_stack=[],
        acceptable_stack=[],
    )

    assert result.preferences_score == 100.0
