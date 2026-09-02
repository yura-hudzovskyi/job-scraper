"""Turning a pile of extracted skill mentions into one clean requirement list.

Both extraction paths — the LLM one and the rules fallback — produce the same raw
material: a skill name, how the posting framed it, and the evidence behind it.
Merging is shared so they can't drift: names go through the ontology, duplicates
collapse, and when the same skill was mentioned twice the stronger framing wins
("nice to have Docker" plus "Docker required" is a requirement, not an option).

See docs/ai-pipeline-v3.md (E2) for why the framing is kept as a type rather than
a bool: "not mentioned" and "mentioned as optional" are different answers, and
neither is a confirmed gap.
"""

from collections.abc import Iterable

from app.domain.jobs.models import NormalizedJobSkill, RequirementType
from app.domain.skills.normalizer import dedupe_key, normalize_skill

# Strongest first — a later mention can only raise a skill's framing, never lower
# it, so one throwaway "nice to have" can't downgrade a hard requirement.
_STRENGTH: dict[RequirementType, int] = {
    RequirementType.REQUIRED_EXPLICIT: 4,
    RequirementType.REQUIRED_INFERRED: 3,
    RequirementType.OPTIONAL_EXPLICIT: 2,
    RequirementType.CONTEXT: 1,
    RequirementType.UNKNOWN: 0,
}


def _stronger(left: NormalizedJobSkill, right: NormalizedJobSkill) -> NormalizedJobSkill:
    if _STRENGTH[right.requirement] > _STRENGTH[left.requirement]:
        winner, loser = right, left
    else:
        winner, loser = left, right
    return NormalizedJobSkill(
        name=winner.name,
        requirement=winner.requirement,
        canonical_id=winner.canonical_id,
        # Keep whatever evidence exists — a mention without a quote shouldn't
        # erase one that had it.
        evidence=winner.evidence or loser.evidence,
        confidence=max(
            winner.confidence if winner.confidence is not None else 0.0,
            loser.confidence if loser.confidence is not None else 0.0,
        )
        or None,
    )


def normalize(
    name: str,
    requirement: RequirementType,
    evidence: str | None = None,
    confidence: float | None = None,
) -> NormalizedJobSkill:
    """One mention, with its name resolved against the ontology."""
    normalized = normalize_skill(name)
    return NormalizedJobSkill(
        name=normalized.name,
        requirement=requirement,
        canonical_id=normalized.canonical_id,
        evidence=evidence,
        confidence=confidence,
    )


def merge(skills: Iterable[NormalizedJobSkill]) -> list[NormalizedJobSkill]:
    """Collapse aliases and repeats, in first-seen order."""
    merged: dict[str, NormalizedJobSkill] = {}
    for skill in skills:
        if not skill.name.strip():
            continue
        key = skill.canonical_id or dedupe_key(skill.name)
        existing = merged.get(key)
        merged[key] = skill if existing is None else _stronger(existing, skill)
    return list(merged.values())
