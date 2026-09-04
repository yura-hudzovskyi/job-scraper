"""The candidate's review of what was extracted from their CV.

Spec 3.5.2 condition 2. The behaviour that matters is that a correction never
overwrites — the extraction behind a past match has to stay readable — and that
a corrected competency is not passed off as something found in the document.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.candidates.models import CvDocument
from app.domain.documents.models import DocumentRevision, EntityKind, RevisionStatus
from app.domain.profiles.models import ProfileKind, ProfileOrigin, ProfileRevision
from app.domain.profiles.schemas import (
    CANDIDATE_PROFILE_SCHEMA_VERSION,
    CandidateProfile,
    CompetencyCategory,
    LinkStatus,
    ProfileQuality,
)
from app.services.profile_review_service import (
    NoCvUploaded,
    NotExtractedYet,
    ProfileReviewService,
    ReviewedCompetency,
)

CV_ID = uuid.uuid4()
REVISION_ID = uuid.uuid4()


class _FakeCandidates:
    def __init__(self, has_cv: bool = True):
        self._has_cv = has_cv

    async def get_active_cv(self, user_id: uuid.UUID) -> CvDocument | None:
        if not self._has_cv:
            return None
        return CvDocument(
            id=str(CV_ID),
            user_id=str(user_id),
            filename="cv.pdf",
            raw_text="Experience with Python",
            uploaded_at=datetime.now(UTC),
        )


class _FakeDocuments:
    def __init__(self, has_revision: bool = True):
        self._has_revision = has_revision

    async def latest(
        self, entity_kind: EntityKind, owner_id: uuid.UUID
    ) -> DocumentRevision | None:
        if not self._has_revision:
            return None
        return DocumentRevision(
            id=str(REVISION_ID),
            entity_kind=EntityKind.CANDIDATE,
            owner_id=str(owner_id),
            revision_no=1,
            content_hash="abc",
            status=RevisionStatus.EXTRACTED,
            raw_text="Experience with Python",
            parsed_text="Experience with Python",
        )


class _FakeProfiles:
    def __init__(self, existing: ProfileRevision | None):
        self._existing = existing
        self.saved: list[dict[str, object]] = []

    async def current(self, document_revision_id: uuid.UUID) -> ProfileRevision | None:
        return self._existing

    async def history(self, document_revision_id: uuid.UUID) -> list[ProfileRevision]:
        return [self._existing] if self._existing else []

    async def save(self, **kwargs: object) -> ProfileRevision:
        self.saved.append(kwargs)
        return ProfileRevision(
            id=str(uuid.uuid4()),
            document_revision_id=str(document_revision_id_of(kwargs)),
            profile_kind=ProfileKind.CANDIDATE,
            schema_version=CANDIDATE_PROFILE_SCHEMA_VERSION,
            origin=ProfileOrigin.USER_OVERRIDE,
            extracted_profile=dict(kwargs["extracted_profile"]),  # type: ignore[arg-type]
        )


def document_revision_id_of(kwargs: dict[str, object]) -> uuid.UUID:
    value = kwargs["document_revision_id"]
    assert isinstance(value, uuid.UUID)
    return value


def _extracted(reviewed: bool = False) -> ProfileRevision:
    profile = CandidateProfile(
        language="en", quality=ProfileQuality(user_reviewed=reviewed)
    )
    return ProfileRevision(
        id=str(uuid.uuid4()),
        document_revision_id=str(REVISION_ID),
        profile_kind=ProfileKind.CANDIDATE,
        schema_version=CANDIDATE_PROFILE_SCHEMA_VERSION,
        origin=ProfileOrigin.STRUCTURAL_EXTRACTION,
        extracted_profile=profile.model_dump(mode="json"),
        extractor_model_id="structural/1.0",
    )


def _service(
    profiles: _FakeProfiles, has_cv: bool = True, has_revision: bool = True
) -> ProfileReviewService:
    return ProfileReviewService(
        _FakeCandidates(has_cv),  # type: ignore[arg-type]
        _FakeDocuments(has_revision),  # type: ignore[arg-type]
        profiles,  # type: ignore[arg-type]
    )


# --- reading -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_extracted_profile_is_returned_with_its_provenance() -> None:
    service = _service(_FakeProfiles(_extracted()))

    view = await service.current(uuid.uuid4())

    assert view.origin is ProfileOrigin.STRUCTURAL_EXTRACTION
    assert view.user_reviewed is False
    assert view.profile.language == "en"


@pytest.mark.asyncio
async def test_no_cv_is_distinguishable_from_nothing_extracted() -> None:
    """Both become a 404, but the detail differs — they mean different things to
    whoever is looking at the screen."""
    with pytest.raises(NoCvUploaded):
        await _service(_FakeProfiles(None), has_cv=False).current(uuid.uuid4())

    with pytest.raises(NotExtractedYet):
        await _service(_FakeProfiles(None)).current(uuid.uuid4())


# --- correcting --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_review_creates_a_new_revision_pointing_at_the_one_it_corrected() -> None:
    """Never overwrites: the extraction behind a past match has to stay
    readable."""
    previous = _extracted()
    profiles = _FakeProfiles(previous)

    await _service(profiles).submit_review(
        uuid.uuid4(), [ReviewedCompetency(raw_text="Python")]
    )

    saved = profiles.saved[0]
    assert saved["origin"] is ProfileOrigin.USER_OVERRIDE
    assert saved["parent_revision_id"] == uuid.UUID(previous.id)


@pytest.mark.asyncio
async def test_a_reviewed_profile_is_marked_reviewed() -> None:
    """Phase 7 gates whether extracted facts may influence a score on this flag,
    so it has to be trustworthy before then."""
    profiles = _FakeProfiles(_extracted())

    view = await _service(profiles).submit_review(uuid.uuid4(), [])

    assert view.user_reviewed is True
    assert view.profile.quality.user_reviewed is True


@pytest.mark.asyncio
async def test_a_user_supplied_competency_carries_no_evidence_and_says_so() -> None:
    """It did not come from the document. Marking it `manual` keeps anything
    downstream from mistaking it for something found in the text."""
    profiles = _FakeProfiles(_extracted())

    view = await _service(profiles).submit_review(
        uuid.uuid4(),
        [ReviewedCompetency(raw_text="Kubernetes", category=CompetencyCategory.TECHNOLOGY)],
    )

    competency = view.profile.competencies[0]
    assert competency.evidence is None
    assert competency.link_status is LinkStatus.MANUAL
    assert competency.category is CompetencyCategory.TECHNOLOGY


@pytest.mark.asyncio
async def test_the_submitted_list_replaces_rather_than_merges() -> None:
    """The screen shows the whole list and the user edits it, so a competency
    they removed has to disappear. Merging would make deletion inexpressible."""
    previous = _extracted()
    previous.extracted_profile["competencies"] = [
        {"raw_text": "COBOL", "category": "technology", "necessity": "unspecified",
         "concept_id": None, "link_status": "unmapped", "link_score": None,
         "confidence": 1.0, "evidence": None}
    ]
    profiles = _FakeProfiles(previous)

    view = await _service(profiles).submit_review(
        uuid.uuid4(), [ReviewedCompetency(raw_text="Python")]
    )

    assert [c.raw_text for c in view.profile.competencies] == ["Python"]


@pytest.mark.asyncio
async def test_a_correction_does_not_claim_a_model_produced_it() -> None:
    profiles = _FakeProfiles(_extracted())

    await _service(profiles).submit_review(uuid.uuid4(), [])

    assert profiles.saved[0]["extractor_model_id"] is None


@pytest.mark.asyncio
async def test_reviewing_without_a_cv_is_refused() -> None:
    with pytest.raises(NoCvUploaded):
        await _service(_FakeProfiles(None), has_cv=False).submit_review(uuid.uuid4(), [])


# --- history -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_returns_the_extraction_and_its_corrections() -> None:
    service = _service(_FakeProfiles(_extracted()))

    revisions = await service.history(uuid.uuid4())

    assert len(revisions) == 1
    assert revisions[0].origin is ProfileOrigin.STRUCTURAL_EXTRACTION


@pytest.mark.asyncio
async def test_history_is_empty_rather_than_an_error_when_nothing_is_stored() -> None:
    service = _service(_FakeProfiles(None), has_revision=False)

    assert await service.history(uuid.uuid4()) == []
