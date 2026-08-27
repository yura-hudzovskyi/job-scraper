from app.domain.candidates.skill_data import build_default_skill_registry
from app.domain.candidates.skills import SkillDefinition, SkillRegistry, SkillRelation


def test_resolves_aliases_case_and_punctuation_insensitively() -> None:
    registry = SkillRegistry(
        [SkillDefinition("javascript", "language", aliases=["JS", "Javascript"])], []
    )
    assert registry.resolve("js") == "javascript"
    assert registry.resolve("JS") == "javascript"
    assert registry.resolve("Javascript") == "javascript"
    assert registry.resolve("javascript") == "javascript"


def test_resolve_returns_none_for_unknown_skill() -> None:
    registry = SkillRegistry([SkillDefinition("python", "language")], [])
    assert registry.resolve("cobol") is None


def test_transferability_is_one_for_identical_skill() -> None:
    registry = SkillRegistry([], [])
    assert registry.transferability("django", "django") == 1.0


def test_transferability_is_zero_for_unrelated_skills() -> None:
    registry = SkillRegistry([], [])
    assert registry.transferability("django", "photoshop") == 0.0


def test_transferability_uses_defined_relation() -> None:
    registry = SkillRegistry([], [SkillRelation("django", "fastapi", 0.7)])
    assert registry.transferability("django", "fastapi") == 0.7
    assert registry.transferability("fastapi", "django") == 0.0  # relation is directional


def test_default_registry_resolves_common_aliases() -> None:
    registry = build_default_skill_registry()
    assert registry.resolve("TypeScript") == "typescript"
    assert registry.resolve("Next.js") == "nextjs"
    assert registry.resolve("Node.js") == "nodejs"
    assert registry.resolve("k8s") == "kubernetes"
    assert registry.resolve("Postgres") == "postgresql"


def test_default_registry_transferability_is_bidirectional() -> None:
    registry = build_default_skill_registry()
    assert registry.transferability("django", "fastapi") == 0.7
    assert registry.transferability("fastapi", "django") == 0.7
    assert registry.transferability("react", "nextjs") == 0.85
