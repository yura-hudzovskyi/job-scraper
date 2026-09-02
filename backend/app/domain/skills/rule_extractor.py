"""Dictionary-and-alias extraction for when no LLM is available — the third step
of the extraction priority order in docs/ai-pipeline-v3.md (A3).

It reads only what the posting literally names: every ontology alias found in the
text, framed by the cue words in the sentence it sits in. It never infers a skill
the text doesn't mention and never produces REQUIRED_INFERRED — inferring is the
one thing rules can't do honestly, and a fabricated requirement turns into a
fabricated gap two stages later.

Confidence is deliberately below what the LLM path reports: cue words in one
sentence are a weaker signal than a model that read the whole posting.
"""

import re
from dataclasses import dataclass

from app.domain.jobs.models import NormalizedJobSkill, RequirementType
from app.domain.skills import requirements
from app.domain.skills.ontology import SKILLS, TAXONOMY_VERSION, Skill

EXTRACTOR_LABEL = f"rules (skills-v{TAXONOMY_VERSION})"

_MAX_EVIDENCE_CHARS = 160
_EXPLICIT_CONFIDENCE = 0.6
_UNKNOWN_CONFIDENCE = 0.3

# Short, common English words that also happen to be language names. Matching
# them the way every other alias is matched would turn "go to production" and
# "C level" into requirements, so they only count inside an obvious technology
# context (a comma/slash-separated stack list, or next to "developer"/"engineer").
_AMBIGUOUS_ALIASES = {"c", "go"}

_REQUIRED_CUES = re.compile(
    r"(?:\brequired\b|\brequirements?\b|\bmust[- ]have\b|\bmandatory\b|\bstrong\b"
    r"|\bsolid\b|\bproficien\w+|\bexpert(?:ise)?\b|\bexperience with\b|\byears?\b"
    r"|обов'?язков\w+|вимоги|досвід)",
    re.IGNORECASE,
)
_OPTIONAL_CUES = re.compile(
    r"(?:\bnice[- ]to[- ]have\b|\bis a plus\b|\bwould be a plus\b|\bplus\b|\bbonus\b"
    r"|\bpreferred\b|\bdesirable\b|\badvantage\b|\boptional\b"
    r"|буде плюсом|перевагою|бажано)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:\n])\s+|\n+")
_TECH_CONTEXT = re.compile(r"[,/|()•·]|\b(?:developer|engineer|lang(?:uage)?|stack)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _AliasPattern:
    skill: Skill
    pattern: re.Pattern[str]
    ambiguous: bool


def _alias_pattern(alias: str) -> re.Pattern[str]:
    """Word-ish boundaries that survive the punctuation real skill names carry:
    `+` and `#` must not count as word characters on either side, or "C" would
    match inside "C++"."""
    return re.compile(rf"(?<![\w+#]){re.escape(alias)}(?![\w+#])", re.IGNORECASE)


def _alias_patterns() -> list[_AliasPattern]:
    patterns: list[_AliasPattern] = []
    for skill in SKILLS:
        for alias in {skill.display, *skill.aliases, skill.id}:
            patterns.append(
                _AliasPattern(
                    skill=skill,
                    pattern=_alias_pattern(alias),
                    ambiguous=alias.lower() in _AMBIGUOUS_ALIASES,
                )
            )
    return patterns


_PATTERNS: list[_AliasPattern] | None = None


def _patterns() -> list[_AliasPattern]:
    global _PATTERNS
    if _PATTERNS is None:
        _PATTERNS = _alias_patterns()
    return _PATTERNS


def _framing(sentence: str) -> tuple[RequirementType, float]:
    """Optional is checked first on purpose: "experience with Kafka is a plus"
    contains a required-sounding cue too, and the weaker framing is the honest
    reading of that sentence."""
    if _OPTIONAL_CUES.search(sentence):
        return RequirementType.OPTIONAL_EXPLICIT, _EXPLICIT_CONFIDENCE
    if _REQUIRED_CUES.search(sentence):
        return RequirementType.REQUIRED_EXPLICIT, _EXPLICIT_CONFIDENCE
    # Mentioned, with nothing saying how it is meant. Not a gap, not a
    # requirement — exactly what UNKNOWN is for.
    return RequirementType.UNKNOWN, _UNKNOWN_CONFIDENCE


def _in_tech_context(sentence: str, match: re.Match[str]) -> bool:
    window = sentence[max(0, match.start() - 20) : match.end() + 20]
    return _TECH_CONTEXT.search(window) is not None


def extract_skills(title: str, description: str) -> list[NormalizedJobSkill]:
    """Every ontology skill the posting names, framed by its sentence. Skills the
    ontology doesn't know are not invented here — that's what the LLM path is
    for."""
    found: list[NormalizedJobSkill] = []
    for sentence in _SENTENCE_SPLIT.split(f"{title}. {description}"):
        stripped = sentence.strip()
        if not stripped:
            continue
        requirement, confidence = _framing(stripped)
        evidence = stripped[:_MAX_EVIDENCE_CHARS]
        for entry in _patterns():
            match = entry.pattern.search(stripped)
            if match is None:
                continue
            if entry.ambiguous and not _in_tech_context(stripped, match):
                continue
            found.append(
                requirements.normalize(
                    name=entry.skill.display,
                    requirement=requirement,
                    evidence=evidence,
                    confidence=confidence,
                )
            )
    return requirements.merge(found)
