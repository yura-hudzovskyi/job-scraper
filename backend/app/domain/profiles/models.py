"""Profile revisions — the extracted, evidence-backed view of a document.

One `document_revision` (the raw text) can produce several `profile_revision`s:
one from the extractor, and then one per user correction. They are append-only
for the same reason the document side is — a match scored against a profile has
to stay explainable after the candidate edits their skills.

Nothing populates these yet. The extractor arrives in Phase 3; this is the shape
it will write into, and the versioning fields exist now so that a profile written
later can never be missing the metadata needed to reproduce it (spec 2.6).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ProfileKind(StrEnum):
    JOB = "job"
    CANDIDATE = "candidate"


class ProfileOrigin(StrEnum):
    """Where a profile revision came from, which is also its trust order.

    A `USER_OVERRIDE` outranks a `NEURAL_EXTRACTION` for the same field: the
    candidate correcting their own CV is better evidence than a model reading it.
    `MIGRATION` marks rows backfilled from an older schema, so they can be told
    apart from anything a model or a person actually produced.
    """

    NEURAL_EXTRACTION = "neural_extraction"
    USER_OVERRIDE = "user_override"
    MIGRATION = "migration"


@dataclass(frozen=True)
class ProfileRevision:
    """A versioned extraction result.

    `extractor_model_id` is not optional in spirit even though it is nullable in
    the column: a revision with `origin = neural_extraction` and no model id is
    unreproducible, and the repository refuses to write one.
    """

    id: str
    document_revision_id: str
    profile_kind: ProfileKind
    schema_version: str
    origin: ProfileOrigin
    extracted_profile: dict[str, Any] = field(default_factory=dict)
    parent_revision_id: str | None = None
    extractor_model_id: str | None = None
    overall_confidence: float | None = None
    validation_warnings: list[str] = field(default_factory=list)
    created_at: datetime | None = None
