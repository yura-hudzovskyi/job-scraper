"""Extraction as an orchestration step, and what it refuses to do on failure.

Three properties from spec 3.5.2 and Phase 3's definition of done, each tested
by making the thing go wrong: a failure must never touch the previous profile, a
span that does not quote the stored text must stop the write rather than be
stored, and a redelivered event must not extract twice.
"""

import uuid

import pytest

from app.domain.documents.models import DocumentRevision, EntityKind, RevisionStatus
from app.domain.profiles.extraction import ExtractionInput, ExtractionResult
from app.domain.profiles.models import ProfileOrigin
from app.domain.profiles.schemas import (
    ConceptMention,
    EvidenceSpan,
    JobProfile,
    ProfileQuality,
)
from app.domain.profiles.structural import StructuralExtractor
from app.services.extraction_service import (
    FAILURE_EXTRACTION_ERROR,
    FAILURE_INVALID_EVIDENCE,
    ExtractionService,
)

PARSED_TEXT = "Потрібен розробник з досвідом 3 роки"


def _revision(status: RevisionStatus = RevisionStatus.PARSED) -> DocumentRevision:
    return DocumentRevision(
        id=str(uuid.uuid4()),
        entity_kind=EntityKind.JOB,
        owner_id=str(uuid.uuid4()),
        revision_no=1,
        content_hash="abc",
        status=status,
        raw_text=PARSED_TEXT,
        parsed_text=PARSED_TEXT,
        language_code="uk",
    )


class _FakeDocuments:
    def __init__(self, revision: DocumentRevision | None, known: dict[str, object] | None = None):
        self._revision = revision
        self._known = known or {}
        self.transitions: list[tuple[RevisionStatus, str | None]] = []

    async def get(self, revision_id: uuid.UUID) -> DocumentRevision | None:
        return self._revision

    async def normalized_fields(self, revision_id: uuid.UUID) -> dict[str, object]:
        return self._known

    async def transition(
        self,
        revision_id: uuid.UUID,
        target: RevisionStatus,
        reason: str | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
    ) -> object:
        self.transitions.append((target, failure_code))
        return object()


class _FakeProfiles:
    def __init__(self) -> None:
        self.saved: list[dict[str, object]] = []

    async def save(self, **kwargs: object) -> object:
        self.saved.append(kwargs)

        class _Saved:
            id = str(uuid.uuid4())

        return _Saved()


class _Exploding:
    async def extract_job(self, document: ExtractionInput) -> ExtractionResult:
        raise RuntimeError("the model fell over")

    async def extract_candidate(self, document: ExtractionInput) -> ExtractionResult:
        raise RuntimeError("the model fell over")


class _Misquoting:
    """Returns a profile whose span is internally valid but points elsewhere."""

    async def extract_job(self, document: ExtractionInput) -> ExtractionResult:
        return ExtractionResult(
            profile=JobProfile(
                competencies=[
                    ConceptMention(
                        raw_text="Python",
                        evidence=EvidenceSpan(start_char=0, end_char=6, text="Python"),
                    )
                ],
                quality=ProfileQuality(overall_confidence=1.0),
            ),
            extractor_model_id="misquoting/1.0",
        )

    async def extract_candidate(self, document: ExtractionInput) -> ExtractionResult:
        raise NotImplementedError


def _service(documents: _FakeDocuments, profiles: _FakeProfiles, extractor: object = None):
    return ExtractionService(
        documents,  # type: ignore[arg-type]
        profiles,  # type: ignore[arg-type]
        extractor or StructuralExtractor(),  # type: ignore[arg-type]
    )


# --- the happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_parsed_revision_is_extracted_and_marked_extracted() -> None:
    revision = _revision()
    documents = _FakeDocuments(revision, {"required_experience_years": 3.0})
    profiles = _FakeProfiles()

    outcome = await _service(documents, profiles).extract(uuid.UUID(revision.id))

    assert outcome.extracted is True
    assert [status for status, _ in documents.transitions] == [
        RevisionStatus.EXTRACTING,
        RevisionStatus.EXTRACTED,
    ]
    assert len(profiles.saved) == 1


@pytest.mark.asyncio
async def test_the_saved_profile_names_what_produced_it() -> None:
    """Without this a stored profile cannot be reproduced or compared against a
    later extractor's output."""
    revision = _revision()
    profiles = _FakeProfiles()

    await _service(_FakeDocuments(revision), profiles).extract(uuid.UUID(revision.id))

    saved = profiles.saved[0]
    assert saved["origin"] is ProfileOrigin.STRUCTURAL_EXTRACTION
    assert saved["extractor_model_id"] == "structural/1.0"


@pytest.mark.asyncio
async def test_rejected_fields_are_stored_alongside_the_profile() -> None:
    """Spec 5.1 step 10 — a field that quietly vanished is indistinguishable
    from one the document never contained."""
    revision = _revision()
    documents = _FakeDocuments(revision, {"salary_min": 6000.0, "salary_max": 1.0})
    profiles = _FakeProfiles()

    await _service(documents, profiles).extract(uuid.UUID(revision.id))

    warnings = profiles.saved[0]["validation_warnings"]
    assert isinstance(warnings, list)
    assert warnings[0]["outcome"] == "rejected"  # type: ignore[index]


# --- what it refuses ---------------------------------------------------------


@pytest.mark.asyncio
async def test_an_extractor_that_raises_fails_the_revision_without_writing() -> None:
    """A failure must never corrupt the active profile — nothing is written, and
    whatever profile existed before stays exactly where it was."""
    revision = _revision()
    documents = _FakeDocuments(revision)
    profiles = _FakeProfiles()

    outcome = await _service(documents, profiles, _Exploding()).extract(uuid.UUID(revision.id))

    assert outcome.extracted is False
    assert profiles.saved == []
    assert documents.transitions[-1] == (RevisionStatus.FAILED, FAILURE_EXTRACTION_ERROR)


@pytest.mark.asyncio
async def test_a_span_that_misquotes_the_stored_text_stops_the_write() -> None:
    """The extractor validated against the text it was handed; this checks
    against the text that is actually stored, which is what keeps the two the
    same when a future extractor builds its own view of the document."""
    revision = _revision()
    documents = _FakeDocuments(revision)
    profiles = _FakeProfiles()

    outcome = await _service(documents, profiles, _Misquoting()).extract(uuid.UUID(revision.id))

    assert outcome.extracted is False
    assert profiles.saved == []
    assert documents.transitions[-1] == (RevisionStatus.FAILED, FAILURE_INVALID_EVIDENCE)


@pytest.mark.asyncio
async def test_a_revision_that_is_not_parsed_is_skipped() -> None:
    """Outbox delivery is at-least-once, so a redelivered event must not extract
    a second time."""
    revision = _revision(status=RevisionStatus.EXTRACTED)
    documents = _FakeDocuments(revision)
    profiles = _FakeProfiles()

    outcome = await _service(documents, profiles).extract(uuid.UUID(revision.id))

    assert outcome.extracted is False
    assert "not parsed" in (outcome.skipped_reason or "")
    assert documents.transitions == []
    assert profiles.saved == []


@pytest.mark.asyncio
async def test_a_missing_revision_is_reported_not_raised() -> None:
    """The relay counts a raised handler as a failure to retry; a revision that
    was purged between the event and its delivery is not going to come back."""
    documents = _FakeDocuments(None)

    outcome = await _service(documents, _FakeProfiles()).extract(uuid.uuid4())

    assert outcome.extracted is False
    assert outcome.skipped_reason == "no such revision"
