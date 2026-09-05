"""The contract every extractor implements, and what comes back from one.

Deliberately says nothing about *how* facts are found. The deterministic
extractor in `structural.py` formalises values the source adapter already
parsed; the GLiNER2 extractor that arrives once Phase 0's benchmark clears the
gate (spec 3.5.2) reads the text itself. Both return the same shape, and the
caller cannot tell which it got — that is the point of the boundary existing
before the model does.

Three buckets rather than one list, per spec 8.4 and 5.1 step 10: a profile
carries only what was accepted, while what was rejected or held for review is
kept alongside for audit. A field that quietly vanished is indistinguishable
from one the document never contained, and only one of those is a bug.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.domain.documents.parsing import ParsedBlock
from app.domain.profiles.schemas import (
    CandidateProfile,
    EvidenceSpan,
    JobProfile,
    Requirement,
)


class FieldOutcome(StrEnum):
    ACCEPTED = "accepted"
    REVIEW = "review"
    REJECTED = "rejected"


@dataclass(frozen=True)
class DiscardedField:
    """Something an extractor produced that did not make it into the profile.

    Stored on the revision so an admin screen can answer "why is this vacancy's
    salary missing" with the reason rather than a shrug.
    """

    kind: str
    outcome: FieldOutcome
    reason: str
    raw_value: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "raw_value": self.raw_value,
        }


@dataclass(frozen=True)
class ExtractionInput:
    """One parsed document, plus whatever the source adapter already knows.

    `known_fields` is how deterministic parsing reaches the extractor without the
    extractor knowing which source produced it. A neural extractor is free to
    ignore it; the structural one is built entirely out of it.
    """

    parsed_text: str
    blocks: Sequence[ParsedBlock] = field(default_factory=list)
    language: str | None = None
    truncated: bool = False
    known_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionResult:
    profile: JobProfile | CandidateProfile
    discarded: list[DiscardedField] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Identifies what produced this, for reproducibility (spec 2.6). For the
    # deterministic extractor this is a ruleset version rather than a model id;
    # the column is named for the common case.
    extractor_model_id: str = ""
    # Which kind of extraction this was, said by the extractor rather than
    # inferred by the caller from the model id. The two origins fail
    # differently (see ProfileOrigin), so a reader deciding how much to trust a
    # field has to be told which one ran — including when a neural extractor
    # degraded to structural output and must not claim otherwise.
    neural: bool = False

    def discarded_records(self) -> list[dict[str, Any]]:
        return [item.as_record() for item in self.discarded]


class ProfileExtractor(Protocol):
    async def extract_job(self, document: ExtractionInput) -> ExtractionResult: ...

    async def extract_candidate(self, document: ExtractionInput) -> ExtractionResult: ...


def find_span(parsed_text: str, needle: str) -> EvidenceSpan | None:
    """Locate a value's own text in the document, case-insensitively.

    This is not a keyword rule. The *conclusion* was reached elsewhere — by the
    source adapter's parsing, or later by a model — and this only finds where in
    the document that conclusion can be pointed at. `if "senior" in title` infers
    seniority from a word; this looks for the word because seniority was already
    established.

    Returns None when the value is not literally present, which is the common
    case for anything derived: the caller then marks the requirement
    `explicit=False` and it can never become a hard filter.
    """
    if not needle or not parsed_text:
        return None

    haystack, target = parsed_text.casefold(), needle.casefold()
    # Case folding is not length-preserving for every script — "İ".casefold() is
    # two characters — and an offset taken from a folded string would then point
    # into the wrong place in the original. Fall back rather than misquote.
    if len(haystack) != len(parsed_text) or len(target) != len(needle):
        haystack, target = parsed_text, needle

    index = haystack.find(target)
    if index < 0:
        return None
    return EvidenceSpan(
        start_char=index,
        end_char=index + len(needle),
        text=parsed_text[index : index + len(needle)],
    )


def reject_unresolvable_spans(
    requirements: list[Requirement], parsed_text: str
) -> tuple[list[Requirement], list[DiscardedField]]:
    """Drop requirements whose evidence does not quote the document.

    The last line of defence before storage. A span can be internally consistent
    and still wrong, and the only thing that can settle it is the text itself —
    so this runs against the real `parsed_text`, not against the extractor's idea
    of it.
    """
    kept: list[Requirement] = []
    discarded: list[DiscardedField] = []
    for requirement in requirements:
        if requirement.evidence is not None and not requirement.evidence.validate_against(
            parsed_text
        ):
            discarded.append(
                DiscardedField(
                    kind=requirement.kind.value,
                    outcome=FieldOutcome.REJECTED,
                    reason="evidence span does not quote the document it points into",
                    raw_value=requirement.evidence.text,
                )
            )
            continue
        kept.append(requirement)
    return kept, discarded
