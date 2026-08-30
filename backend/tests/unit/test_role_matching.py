import pytest

from app.domain.matching.role_matching import RoleMatcher


class _FakeEmbeddingProvider:
    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


@pytest.mark.asyncio
async def test_rewards_close_title_match() -> None:
    vectors = {
        "Senior Full Stack Engineer": [1.0, 0.0],
        "full_stack": [1.0, 0.0],
        "data_scientist": [0.0, 1.0],
    }
    matcher = RoleMatcher(_FakeEmbeddingProvider(vectors))

    exact = await matcher.assess("Senior Full Stack Engineer", ["full_stack"], [])
    unrelated = await matcher.assess("Senior Full Stack Engineer", ["data_scientist"], [])

    assert exact > unrelated


@pytest.mark.asyncio
async def test_defaults_to_full_marks_with_no_preference_or_profile_roles() -> None:
    matcher = RoleMatcher(_FakeEmbeddingProvider({}))
    role = await matcher.assess("Senior Python Developer", [], [])
    assert role == 100.0


@pytest.mark.asyncio
async def test_falls_back_to_profile_roles_without_preference() -> None:
    # No preferred_roles configured, but the CV-derived profile says "Backend
    # Developer" — an "Account Manager" posting must not get a free pass just
    # because the candidate never filled in a role preference.
    vectors = {"Account Manager": [0.0, 1.0], "Backend Developer": [1.0, 0.0]}
    matcher = RoleMatcher(_FakeEmbeddingProvider(vectors))

    role = await matcher.assess("Account Manager", [], ["Backend Developer"])

    assert role < 50.0


@pytest.mark.asyncio
async def test_explicit_preference_wins_over_profile_roles() -> None:
    # Only job title + preferred role need vectors — if the implementation
    # mistakenly fell back to profile roles despite an explicit preference being
    # set, this would KeyError instead of silently passing.
    vectors = {"Senior Full Stack Engineer": [1.0, 0.0], "full_stack": [1.0, 0.0]}
    matcher = RoleMatcher(_FakeEmbeddingProvider(vectors))

    role_a = await matcher.assess(
        "Senior Full Stack Engineer", ["full_stack"], ["Full Stack Engineer"]
    )
    role_b = await matcher.assess("Senior Full Stack Engineer", ["full_stack"], ["Data Scientist"])

    assert role_a == role_b
