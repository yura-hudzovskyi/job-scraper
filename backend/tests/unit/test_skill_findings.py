"""The typed outcomes behind the old score triple: what counts as held, what
counts as adjacent, and — the one that matters most — what is not a gap at all.
"""

import zlib

import pytest

from app.domain.jobs.models import NormalizedJobSkill, RequirementType
from app.domain.matching.skill_matching import SkillMatcher, SkillOutcome

_DIMENSIONS = 512


def _orthogonal(text: str) -> list[float]:
    """A distinct one-hot vector per text. A shared default would make every
    unlisted skill perfectly similar to every other one, which silently turns
    these tests into "everything matches"."""
    vector = [0.0] * _DIMENSIONS
    vector[zlib.crc32(text.encode()) % _DIMENSIONS] = 1.0
    return vector


class _FakeEmbeddingProvider:
    """Anything not named explicitly is orthogonal to everything else, so
    similarity only enters a test that asks for it."""

    def __init__(self, vectors: dict[str, list[float]] | None = None):
        self._vectors = vectors or {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors.get(text) or _orthogonal(text) for text in texts]


async def _findings(job_skills, candidate_skills, vectors=None):
    matcher = SkillMatcher(_FakeEmbeddingProvider(vectors))  # type: ignore[arg-type]
    assessment = await matcher.assess(
        job_skills=job_skills,
        candidate_skills=candidate_skills,
        preferred_stack=[],
        acceptable_stack=[],
    )
    return {finding.name: finding for finding in assessment.findings}, assessment


@pytest.mark.asyncio
async def test_the_same_skill_spelled_differently_is_matched_without_embeddings() -> None:
    findings, _ = await _findings(
        [NormalizedJobSkill(name="PostgreSQL", requirement=RequirementType.REQUIRED_EXPLICIT)],
        ["Postgres"],
    )

    assert findings["PostgreSQL"].outcome is SkillOutcome.MATCHED
    assert findings["PostgreSQL"].similarity == 1.0


@pytest.mark.asyncio
async def test_a_skill_the_candidate_has_evidence_for_counts_as_matched() -> None:
    # TypeScript is evidence for JavaScript. The reverse is covered below.
    findings, _ = await _findings(
        [NormalizedJobSkill(name="JavaScript", requirement=RequirementType.REQUIRED_EXPLICIT)],
        ["TypeScript"],
    )

    assert findings["JavaScript"].outcome is SkillOutcome.MATCHED_EQUIVALENT


@pytest.mark.asyncio
async def test_the_implication_does_not_run_backwards() -> None:
    findings, _ = await _findings(
        [NormalizedJobSkill(name="TypeScript", requirement=RequirementType.REQUIRED_EXPLICIT)],
        ["JavaScript"],
    )

    assert findings["TypeScript"].outcome is SkillOutcome.MISSING


@pytest.mark.asyncio
async def test_related_experience_is_partial_not_a_match() -> None:
    findings, _ = await _findings(
        [NormalizedJobSkill(name="Kubernetes", requirement=RequirementType.REQUIRED_EXPLICIT)],
        ["Docker"],
    )

    assert findings["Kubernetes"].outcome is SkillOutcome.PARTIAL
    assert findings["Kubernetes"].satisfied is False


@pytest.mark.asyncio
async def test_a_mention_the_posting_never_framed_is_not_a_gap() -> None:
    # The single most important line here: an unknown reported as a missing skill
    # is a fabricated gap.
    findings, assessment = await _findings(
        [
            NormalizedJobSkill(name="Rust", requirement=RequirementType.UNKNOWN),
            NormalizedJobSkill(name="Grafana", requirement=RequirementType.CONTEXT),
        ],
        ["Python"],
    )

    assert findings["Rust"].outcome is SkillOutcome.UNKNOWN
    assert findings["Grafana"].outcome is SkillOutcome.UNKNOWN
    assert findings["Rust"].is_gap is False
    assert assessment.gaps == []


@pytest.mark.asyncio
async def test_unknown_requirements_do_not_count_against_required_coverage() -> None:
    _, assessment = await _findings(
        [
            NormalizedJobSkill(name="Python", requirement=RequirementType.REQUIRED_EXPLICIT),
            NormalizedJobSkill(name="Rust", requirement=RequirementType.UNKNOWN),
        ],
        ["Python"],
    )

    assert assessment.required_coverage == 1.0


@pytest.mark.asyncio
async def test_a_missing_requirement_lowers_required_coverage() -> None:
    _, assessment = await _findings(
        [
            NormalizedJobSkill(name="Python", requirement=RequirementType.REQUIRED_EXPLICIT),
            NormalizedJobSkill(name="Elixir", requirement=RequirementType.REQUIRED_EXPLICIT),
        ],
        ["Python"],
    )

    assert assessment.required_coverage == 0.5


@pytest.mark.asyncio
async def test_an_optional_skill_the_candidate_lacks_is_not_required_coverage() -> None:
    _, assessment = await _findings(
        [
            NormalizedJobSkill(name="Python", requirement=RequirementType.REQUIRED_EXPLICIT),
            NormalizedJobSkill(name="Elixir", requirement=RequirementType.OPTIONAL_EXPLICIT),
        ],
        ["Python"],
    )

    assert assessment.required_coverage == 1.0
    assert assessment.gaps == [("Elixir", False)]


@pytest.mark.asyncio
async def test_similarity_still_covers_skills_the_ontology_never_heard_of() -> None:
    findings, _ = await _findings(
        [NormalizedJobSkill(name="Adobe After Effects", requirement=RequirementType.REQUIRED_EXPLICIT)],
        ["After Effects"],
        vectors={"Adobe After Effects": [1.0, 0.0], "After Effects": [0.99, 0.14]},
    )

    assert findings["Adobe After Effects"].outcome is SkillOutcome.MATCHED
    assert findings["Adobe After Effects"].matched_by == "After Effects"


@pytest.mark.asyncio
async def test_the_evidence_quote_travels_with_the_finding() -> None:
    findings, _ = await _findings(
        [
            NormalizedJobSkill(
                name="Python",
                requirement=RequirementType.REQUIRED_EXPLICIT,
                evidence="5+ years of Python required.",
            )
        ],
        ["Python"],
    )

    assert findings["Python"].evidence == "5+ years of Python required."
