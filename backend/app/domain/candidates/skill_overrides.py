"""Extraction proposes, the user decides — see docs/ai-pipeline-v3.md (A4):
"user corrections outrank every automated extraction and must survive
reprocessing".

A CandidateProfile is an immutable snapshot of one analysis, so a correction
stored *in* a snapshot would be lost the next time the CV is read. Corrections
therefore live next to the user as SkillOverrides and are re-applied to every
extraction, which is also why they can express a removal: "the CV mentions
jQuery, don't count it" is a correction, not an absence.
"""

from collections.abc import Iterable

from app.domain.candidates.models import CandidateSkill, SkillLevel, SkillOverride, SkillSource
from app.domain.skills.normalizer import dedupe_key, normalize_skill

_DEFAULT_ADDED_LEVEL = SkillLevel.COMMERCIAL


def normalize(skills: Iterable[CandidateSkill]) -> list[CandidateSkill]:
    """Canonical names, duplicates collapsed — the same treatment job
    requirements get, so both sides of a match speak one vocabulary. The first
    mention wins: an LLM listing "React" twice means one skill, not two."""
    result: dict[str, CandidateSkill] = {}
    for skill in skills:
        if not skill.name.strip():
            continue
        normalized = normalize_skill(skill.name)
        key = normalized.canonical_id or dedupe_key(skill.name)
        result.setdefault(key, CandidateSkill(
            name=normalized.name,
            level=skill.level,
            years=skill.years,
            source=skill.source,
        ))
    return list(result.values())


def apply_overrides(
    skills: Iterable[CandidateSkill], overrides: Iterable[SkillOverride]
) -> list[CandidateSkill]:
    """The extracted list with the user's decisions layered on: removals dropped,
    edited levels/years replaced, and anything the user added appended. Every
    skill the user touched comes back marked USER, so provenance shows who said
    so."""
    by_key = {override.skill_key: override for override in overrides}
    used: set[str] = set()
    result: list[CandidateSkill] = []

    for skill in skills:
        key = dedupe_key(skill.name)
        override = by_key.get(key)
        if override is None:
            result.append(skill)
            continue
        used.add(key)
        if override.removed:
            continue
        result.append(
            CandidateSkill(
                name=override.name or skill.name,
                # An override that only says "keep this" leaves the extracted
                # level and years alone rather than resetting them.
                level=override.level or skill.level,
                years=override.years if override.years is not None else skill.years,
                source=SkillSource.USER,
            )
        )

    for override in by_key.values():
        if override.skill_key in used or override.removed:
            continue
        result.append(
            CandidateSkill(
                name=override.name,
                level=override.level or _DEFAULT_ADDED_LEVEL,
                years=override.years,
                source=SkillSource.USER,
            )
        )
    return result
