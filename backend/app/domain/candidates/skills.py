"""Skill ontology: alias resolution + transferability between related skills.

Collapses free-text variants ("JS", "Javascript", "JavaScript") into one canonical
skill, and records a transferability weight between related-but-distinct skills
(e.g. django -> nestjs) so the matching engine can treat a framework gap differently
from a fundamental capability gap. See docs/matching-engine.md.
"""

import re
from dataclasses import dataclass, field

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize(name: str) -> str:
    return _NORMALIZE_RE.sub("", name.lower())


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
        self._definitions = {d.canonical_name: d for d in definitions}
        self._alias_index: dict[str, str] = {}
        for definition in definitions:
            self._alias_index[_normalize(definition.canonical_name)] = definition.canonical_name
            for alias in definition.aliases:
                self._alias_index[_normalize(alias)] = definition.canonical_name

        self._relations: dict[tuple[str, str], float] = {
            (relation.from_skill, relation.to_skill): relation.transferability
            for relation in relations
        }

    def resolve(self, raw_skill_name: str) -> str | None:
        """Return the canonical skill name for a raw/alias string, or None if unknown."""
        return self._alias_index.get(_normalize(raw_skill_name))

    def transferability(self, from_skill: str, to_skill: str) -> float:
        """Return the transferability weight between two canonical skills (1.0 if
        identical, 0.0 if unrelated or unknown)."""
        if from_skill == to_skill:
            return 1.0
        return self._relations.get((from_skill, to_skill), 0.0)

    def category_of(self, canonical_name: str) -> str | None:
        definition = self._definitions.get(canonical_name)
        return definition.category if definition else None
