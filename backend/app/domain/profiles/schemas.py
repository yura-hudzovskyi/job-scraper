"""Versioned schemas for what an extractor produces.

These validate `profile_revisions.extracted_profile` before it is stored, and
they are versioned because a stored revision has to stay readable by the code
that wrote it. When a field changes shape, the version goes up and old rows keep
validating against the old schema — that is the whole reason the column carries
`schema_version` rather than just JSON.

Pydantic rather than dataclasses, unlike the rest of `domain/`: this is the one
place where the job is *validating untrusted structure* on the way into a JSONB
column, and rejecting a malformed profile at the boundary is exactly what
pydantic is for. It is not a framework dependency — no FastAPI, no HTTP.

The invariant worth stating up front: an `EvidenceSpan` must quote the document
it points into. `Requirement.evidence` being present is a claim that a human can
be shown that substring as the reason a match was made, so the offsets are
validated on construction and checked against the real text before storage
(see `validate_against`).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

JOB_PROFILE_SCHEMA_VERSION = "job-profile/1.0"
CANDIDATE_PROFILE_SCHEMA_VERSION = "candidate-profile/1.0"


class Necessity(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    UNSPECIFIED = "unspecified"


class RequirementKind(StrEnum):
    COMPETENCY = "competency"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    CREDENTIAL = "credential"
    LANGUAGE = "language"
    LOCATION = "location"
    WORK_AUTHORIZATION = "work_authorization"
    SCHEDULE = "schedule"
    COMPENSATION = "compensation"
    EMPLOYMENT_TYPE = "employment_type"
    PHYSICAL = "physical"
    OTHER = "other"


class RequirementOperator(StrEnum):
    HAS = "has"
    AT_LEAST = "at_least"
    AT_MOST = "at_most"
    ONE_OF = "one_of"
    ALL_OF = "all_of"
    NOT = "not"


class CompetencyCategory(StrEnum):
    """Deliberately not IT-specific — spec 2.1. A driving category and a nursing
    licence have to fit here as naturally as a framework does."""

    PROFESSIONAL_SKILL = "professional_skill"
    TOOL = "tool"
    TECHNOLOGY = "technology"
    METHODOLOGY = "methodology"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    REGULATION_KNOWLEDGE = "regulation_knowledge"
    TRANSVERSAL_SKILL = "transversal_skill"
    PHYSICAL_SKILL = "physical_skill"
    OTHER = "other"


class LinkStatus(StrEnum):
    LINKED = "linked"
    AMBIGUOUS = "ambiguous"
    UNMAPPED = "unmapped"
    MANUAL = "manual"


class _Strict(BaseModel):
    """Rejects unknown keys. An extractor that starts emitting a field nobody
    reads should fail loudly at the boundary rather than have it silently
    stored and silently ignored."""

    model_config = ConfigDict(extra="forbid")


class EvidenceSpan(_Strict):
    """Where in the document a fact was read from.

    Offsets index into the revision's `parsed_text`, never into the raw text:
    that is what block offsets from Phase 2 are relative to, and mixing the two
    would quote the wrong substring while staying in range.
    """

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text: str = Field(min_length=1)
    block_id: str | None = None
    page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _end_after_start(self) -> "EvidenceSpan":
        if self.end_char <= self.start_char:
            raise ValueError(
                f"evidence span ends at {self.end_char}, which is not after its "
                f"start at {self.start_char}"
            )
        if self.end_char - self.start_char != len(self.text):
            raise ValueError(
                f"evidence span covers {self.end_char - self.start_char} characters "
                f"but quotes {len(self.text)}"
            )
        return self

    def validate_against(self, parsed_text: str) -> bool:
        """Whether this span really quotes that document.

        Checked before storage. A span that passes construction can still be
        wrong — the offsets are self-consistent but point somewhere else — and
        only the document can settle it.
        """
        return parsed_text[self.start_char : self.end_char] == self.text


class ConceptMention(_Strict):
    """A skill, tool or qualification named in the text.

    `concept_id` is null until Phase 4 links it against ESCO. `link_status`
    starts at `unmapped` for the same reason, and that is an honest state rather
    than a placeholder: a mention with no concept is still evidence that the word
    appeared, which is what the lexical channel matches on.
    """

    raw_text: str = Field(min_length=1)
    category: CompetencyCategory = CompetencyCategory.OTHER
    necessity: Necessity = Necessity.UNSPECIFIED
    concept_id: str | None = None
    link_status: LinkStatus = LinkStatus.UNMAPPED
    link_score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: EvidenceSpan | None = None


class Requirement(_Strict):
    """One thing a vacancy asks for, or one thing a candidate has.

    `explicit` is the load-bearing flag. False means the value was derived rather
    than read from the text — it may still be right, but it can never become a
    hard filter (spec 2.4), because a filter that rejects a vacancy has to be
    able to point at the sentence it rejected it for.
    """

    kind: RequirementKind
    necessity: Necessity = Necessity.UNSPECIFIED
    operator: RequirementOperator = RequirementOperator.HAS
    value: dict[str, Any] = Field(default_factory=dict)
    explicit: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: EvidenceSpan | None = None

    @model_validator(mode="after")
    def _explicit_needs_evidence(self) -> "Requirement":
        """An explicit requirement without a span would be a claim that the text
        says something, with nothing to show for it."""
        if self.explicit and self.evidence is None:
            raise ValueError(
                f"requirement {self.kind} is marked explicit but carries no evidence span; "
                "derived values must set explicit=False"
            )
        return self


class ProfileQuality(_Strict):
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    # True once the candidate has seen and confirmed what was extracted. Spec
    # 3.5.2 condition 2: extracted facts must not reach a score before this.
    user_reviewed: bool = False
    document_truncated: bool = False


class JobProfile(_Strict):
    schema_version: str = JOB_PROFILE_SCHEMA_VERSION
    language: str | None = None
    display_title: str | None = None
    seniority: str | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    competencies: list[ConceptMention] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    quality: ProfileQuality = Field(default_factory=ProfileQuality)


class CandidateProfile(_Strict):
    schema_version: str = CANDIDATE_PROFILE_SCHEMA_VERSION
    language: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    competencies: list[ConceptMention] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    quality: ProfileQuality = Field(default_factory=ProfileQuality)


def spans_of(profile: JobProfile | CandidateProfile) -> list[EvidenceSpan]:
    """Every evidence span in a profile, wherever it hangs.

    Used to check a whole profile against its document in one pass before
    storage; a caller doing this by hand would eventually add a field and forget
    to walk it.
    """
    spans: list[EvidenceSpan] = []
    if isinstance(profile, JobProfile):
        spans.extend(
            requirement.evidence
            for requirement in profile.requirements
            if requirement.evidence is not None
        )
    spans.extend(
        competency.evidence
        for competency in profile.competencies
        if competency.evidence is not None
    )
    return spans
