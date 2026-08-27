"""Skill ontology: alias resolution + transferability between related skills.

Collapses free-text variants ("JS", "Javascript", "JavaScript") into one canonical
skill, and records a transferability weight between related-but-distinct skills
(e.g. django -> nestjs) so the matching engine can treat a framework gap differently
from a fundamental capability gap. See docs/matching-engine.md.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillDefinition:
    canonical_name: str
    category: str
    aliases: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillRelation:
    from_skill: str
    to_skill: str
    transferability: float  # 0.0 - 1.0


class SkillRegistry:
    """Resolves free-text skill names to canonical skills and looks up transferability."""

    def __init__(self, definitions: list[SkillDefinition], relations: list[SkillRelation]):
        self._definitions = definitions
        self._relations = relations

    def resolve(self, raw_skill_name: str) -> str | None:
        """Return the canonical skill name for a raw/alias string, or None if unknown."""
        raise NotImplementedError

    def transferability(self, from_skill: str, to_skill: str) -> float:
        """Return the transferability weight between two canonical skills (0 if unrelated)."""
        raise NotImplementedError
