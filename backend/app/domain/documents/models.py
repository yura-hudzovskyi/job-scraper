"""Immutable document revisions — every version of a job posting or a CV we saw.

The pipeline's raw side is append-only. A vacancy whose text changes does not
overwrite what we stored yesterday; it gets revision `n+1`, and yesterday's text
stays exactly as fetched. That is what makes a match reproducible after the fact:
a score computed against revision 2 can still be explained when the source has
moved on to revision 5.

Nothing here talks to SQLAlchemy or Celery. `plan_revision` and the transition
table are the two decisions worth testing on their own, so they are pure
functions over plain data and the repository does the persisting.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


def compute_content_hash(raw_text: str) -> str:
    """The identity of one version of a document.

    Taken over the *raw* text a source gave us, never over the parsed text.
    `parsed_text` is a function of (raw_text, parser_version), and both of those
    are stored separately — so hashing the raw text means a parser improvement
    re-parses existing revisions instead of manufacturing a new one for every
    document in the corpus on the day it ships.

    Full sha256 hex, matching `encode(sha256(...), 'hex')` in the Phase 1
    backfill migration. The two must agree or every backfilled document looks
    changed on its next scrape.
    """
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


class EntityKind(StrEnum):
    """What a revision is a revision *of*. A revision row carries exactly one
    owner FK, and this says which one is set — see the check constraint on
    `document_revisions`."""

    JOB = "job"
    CANDIDATE = "candidate"


class RevisionStatus(StrEnum):
    """Where a revision is in the processing pipeline.

    `SEARCHABLE` is the only state in which a revision may be used for matching.
    `FAILED` is not terminal: it records that a stage gave up, and a retry rewinds
    to that stage's input state (see ALLOWED_TRANSITIONS).
    """

    RECEIVED = "received"
    PARSED = "parsed"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    INDEXING = "indexing"
    SEARCHABLE = "searchable"
    FAILED = "failed"


class BlockType(StrEnum):
    """What a parsed block is, structurally — never what it means. "This block is
    a heading" is layout; "this heading introduces required skills" is extraction,
    and belongs to the extractor, not here."""

    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE_CELL = "table_cell"
    METADATA = "metadata"
    UNKNOWN = "unknown"


# The forward path is linear: received -> parsed -> extracting -> extracted ->
# indexing -> searchable. Two edges are worth explaining because they are the
# ones a reader would otherwise assume are missing:
#
#   SEARCHABLE -> PARSED   a reprocess. The raw text has not changed, so there is
#                          no new revision to create; re-extracting under a new
#                          model version rewinds to the parsed text and runs
#                          forward again.
#   FAILED -> {RECEIVED, PARSED, EXTRACTED}
#                          a retry rewinds to the *input* state of whichever stage
#                          failed, rather than resuming mid-stage. Parsing consumes
#                          RECEIVED, extraction consumes PARSED, indexing consumes
#                          EXTRACTED.
ALLOWED_TRANSITIONS: dict[RevisionStatus, frozenset[RevisionStatus]] = {
    RevisionStatus.RECEIVED: frozenset({RevisionStatus.PARSED, RevisionStatus.FAILED}),
    RevisionStatus.PARSED: frozenset({RevisionStatus.EXTRACTING, RevisionStatus.FAILED}),
    RevisionStatus.EXTRACTING: frozenset({RevisionStatus.EXTRACTED, RevisionStatus.FAILED}),
    RevisionStatus.EXTRACTED: frozenset({RevisionStatus.INDEXING, RevisionStatus.FAILED}),
    RevisionStatus.INDEXING: frozenset({RevisionStatus.SEARCHABLE, RevisionStatus.FAILED}),
    RevisionStatus.SEARCHABLE: frozenset({RevisionStatus.PARSED, RevisionStatus.FAILED}),
    RevisionStatus.FAILED: frozenset(
        {RevisionStatus.RECEIVED, RevisionStatus.PARSED, RevisionStatus.EXTRACTED}
    ),
}


class IllegalTransition(ValueError):
    """Raised instead of silently writing a state nothing can reach. A revision
    that jumps straight from `received` to `searchable` has skipped extraction,
    and the resulting profile would be missing rather than wrong — which is worse,
    because nothing downstream would notice."""

    def __init__(self, current: RevisionStatus, target: RevisionStatus):
        super().__init__(f"cannot move a revision from {current} to {target}")
        self.current = current
        self.target = target


def can_transition(current: RevisionStatus, target: RevisionStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def check_transition(current: RevisionStatus, target: RevisionStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


@dataclass(frozen=True)
class RevisionRef:
    """The two facts about a stored revision that decide what a re-ingest does."""

    revision_no: int
    content_hash: str


@dataclass(frozen=True)
class RevisionPlan:
    """What ingesting a given piece of content should do to a revision history.

    `is_new` False means the caller writes nothing: this exact text is already
    stored, and re-fetching it is an idempotent no-op rather than a version bump.
    """

    revision_no: int
    is_new: bool
    # True when the content matches a revision that is *not* the newest one — the
    # source reverted to text we had seen before. Still not a new revision (the
    # bytes are not new), but callers that track "which revision is current"
    # need to know the pointer moves backwards rather than forwards.
    reverted: bool = False


def plan_revision(history: Sequence[RevisionRef], content_hash: str) -> RevisionPlan:
    """Decide whether this content is a new revision, and which number it gets.

    Re-fetching unchanged text is by far the common case — a scrape re-reads the
    same vacancy every run — so it must cost nothing and create nothing.
    """
    if not history:
        return RevisionPlan(revision_no=1, is_new=True)

    latest = max(history, key=lambda revision: revision.revision_no)
    if latest.content_hash == content_hash:
        return RevisionPlan(revision_no=latest.revision_no, is_new=False)

    for revision in history:
        if revision.content_hash == content_hash:
            return RevisionPlan(revision_no=revision.revision_no, is_new=False, reverted=True)

    return RevisionPlan(revision_no=latest.revision_no + 1, is_new=True)


@dataclass(frozen=True)
class DocumentRevision:
    """One immutable version of a source document.

    `raw_text` is what the source gave us; `parsed_text` is what block parsing
    produced from it (Phase 2). Both are kept: an offset in an evidence span is
    only meaningful against a text that never changes.
    """

    id: str
    entity_kind: EntityKind
    owner_id: str
    revision_no: int
    content_hash: str
    status: RevisionStatus
    raw_text: str
    parsed_text: str | None = None
    language_code: str | None = None
    mime_type: str | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    failure_code: str | None = None
    failure_detail: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class DocumentBlock:
    """A parsed span of a revision, with offsets into that revision's text.

    `start_char`/`end_char` are global offsets into `parsed_text`, not into the
    block itself — an evidence span produced later has to be resolvable back to
    the exact substring of the document a human can be shown.
    """

    id: str
    document_revision_id: str
    ordinal: int
    block_type: BlockType
    text: str
    start_char: int
    end_char: int
    page: int | None = None
