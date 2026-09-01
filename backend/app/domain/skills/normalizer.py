"""Turns whatever a posting or a CV called a skill into one stable name.

"React.js", "ReactJS" and "react js" are the same requirement; storing all three
means the same gap gets reported three ways and the same strength never dedupes.
The normalizer maps a raw string to its canonical ontology entry when there is
one, and otherwise just tidies the string up — an unknown skill stays a skill,
it simply keeps its own name (see ontology.py).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.skills.ontology import Skill, by_key

# Everything that isn't a letter, digit, +, # or . becomes a space; those three
# survive because they carry meaning in real skill names (C++, C#, Node.js).
_SEPARATORS = re.compile(r"[^a-z0-9+#.]+")
_TRAILING_JUNK = re.compile(r"[.\s]+$")


def lookup_key(name: str) -> str:
    """The shape both the ontology index and every lookup agree on: lowercase,
    punctuation-as-space, and the dotted/suffixed spellings of the same word
    collapsed, so "Node.js", "node js", "NodeJS" and "node" all meet."""
    text = _SEPARATORS.sub(" ", name.strip().lower())
    text = _TRAILING_JUNK.sub("", text).strip()
    # "node.js" -> "node js" -> "node"; same for the js/ts suffixes people attach
    # to framework names. Done on the whole string so "react native" is untouched.
    text = re.sub(r"\b(\w+)(?:\s*\.\s*|\s+)?(?:js|jsx)\b", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class NormalizedSkill:
    """`name` is what to store and show; `canonical_id` is set only when the
    ontology recognized it, and is what evidence and relations key off."""

    name: str
    canonical_id: str | None

    @property
    def known(self) -> bool:
        return self.canonical_id is not None


def normalize_skill(raw: str) -> NormalizedSkill:
    key = lookup_key(raw)
    skill: Skill | None = by_key(key)
    if skill is not None:
        return NormalizedSkill(name=skill.display, canonical_id=skill.id)
    # Unknown: keep the original wording (trimmed) rather than the lookup key, so
    # "Adobe After Effects" doesn't come back as "adobe after effects".
    return NormalizedSkill(name=" ".join(raw.split()), canonical_id=None)


def dedupe_key(raw: str) -> str:
    """What makes two mentions "the same skill" — the canonical id when known,
    the lookup key otherwise."""
    normalized = normalize_skill(raw)
    return normalized.canonical_id or lookup_key(raw)


def unique_skills(names: Iterable[str]) -> list[NormalizedSkill]:
    """Normalized, in first-seen order, with duplicates and aliases collapsed."""
    seen: set[str] = set()
    result: list[NormalizedSkill] = []
    for name in names:
        if not name.strip():
            continue
        key = dedupe_key(name)
        if key in seen:
            continue
        seen.add(key)
        result.append(normalize_skill(name))
    return result
