"""Use case: show a candidate what was extracted from their CV, and let them fix it.

This is not a nice-to-have screen. Spec 3.5.2 makes it one of three conditions
that distinguish this extraction layer from the one removed in b5569d7, and the
reason is specific: the previous layer put a model's skill list behind a
confident-looking verdict with no way for the person it described to say "that
is wrong". A correction here outranks anything automated for the same field.

Corrections never overwrite. A review creates a new profile revision pointing at
the one it corrected, so the extraction that produced a past match stays
readable — the same rule the document side follows, for the same reason.

A confirmed profile is marked `user_reviewed`. Nothing consumes that flag yet;
Phase 7 is where it gates whether extracted facts may influence a score, and the
flag has to exist and be trustworthy before then rather than be retrofitted onto
profiles that were never actually reviewed.
"""

import uuid
from dataclasses import dataclass

from app.domain.documents.models import EntityKind
from app.domain.profiles.models import ProfileKind, ProfileOrigin, ProfileRevision
from app.domain.profiles.schemas import (
    CANDIDATE_PROFILE_SCHEMA_VERSION,
    CandidateProfile,
    CompetencyCategory,
    ConceptMention,
    LinkStatus,
)
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.profile_repository import ProfileRepository


class NoCvUploaded(LookupError):
    """There is nothing to review yet. A 404 rather than an empty profile: the
    two mean different things to someone looking at the screen."""


class NotExtractedYet(LookupError):
    """The CV is stored but extraction has not run or has not finished. Distinct
    from "extracted and found nothing", which is a real answer."""


@dataclass(frozen=True)
class ReviewedCompetency:
    raw_text: str
    category: CompetencyCategory = CompetencyCategory.OTHER


@dataclass(frozen=True)
class ExtractedProfileView:
    document_revision_id: str
    profile_revision_id: str
    profile: CandidateProfile
    origin: ProfileOrigin
    user_reviewed: bool


class ProfileReviewService:
    def __init__(
        self,
        candidate_repository: CandidateRepository,
        document_repository: DocumentRepository,
        profile_repository: ProfileRepository,
    ):
        self._candidates = candidate_repository
        self._documents = document_repository
        self._profiles = profile_repository

    async def current(self, user_id: uuid.UUID) -> ExtractedProfileView:
        """What was extracted from the candidate's active CV."""
        revision, profile_revision = await self._current_pair(user_id)
        return ExtractedProfileView(
            document_revision_id=revision,
            profile_revision_id=profile_revision.id,
            profile=CandidateProfile.model_validate(profile_revision.extracted_profile),
            origin=profile_revision.origin,
            user_reviewed=_reviewed(profile_revision),
        )

    async def submit_review(
        self,
        user_id: uuid.UUID,
        competencies: list[ReviewedCompetency],
    ) -> ExtractedProfileView:
        """Record the candidate's corrections as a new revision.

        The competencies they submit replace the list wholesale rather than
        merging: a review screen shows the full list and the user edits it, so a
        competency they removed has to disappear. Merging would make deletion
        impossible to express.

        A user-supplied competency carries no evidence span, and that is honest
        rather than a gap — it did not come from the document. `link_status`
        records it as `manual` so nothing downstream mistakes it for something
        found in the text.
        """
        document_revision_id, previous = await self._current_pair(user_id)
        base = CandidateProfile.model_validate(previous.extracted_profile)

        corrected = base.model_copy(
            update={
                "competencies": [
                    ConceptMention(
                        raw_text=competency.raw_text,
                        category=competency.category,
                        link_status=LinkStatus.MANUAL,
                        evidence=None,
                    )
                    for competency in competencies
                ],
                "quality": base.quality.model_copy(update={"user_reviewed": True}),
            }
        )

        saved = await self._profiles.save(
            document_revision_id=uuid.UUID(document_revision_id),
            profile_kind=ProfileKind.CANDIDATE,
            schema_version=CANDIDATE_PROFILE_SCHEMA_VERSION,
            origin=ProfileOrigin.USER_OVERRIDE,
            extracted_profile=corrected.model_dump(mode="json"),
            # No extractor produced this, and the check constraint only requires
            # a model id for automated origins. Claiming one would say a model
            # made an edit a person made.
            extractor_model_id=None,
            overall_confidence=corrected.quality.overall_confidence,
            parent_revision_id=uuid.UUID(previous.id),
        )
        return ExtractedProfileView(
            document_revision_id=document_revision_id,
            profile_revision_id=saved.id,
            profile=corrected,
            origin=saved.origin,
            user_reviewed=True,
        )

    async def history(self, user_id: uuid.UUID) -> list[ProfileRevision]:
        """Every version of this CV's profile, oldest first — the extraction and
        each correction made to it."""
        cv = await self._candidates.get_active_cv(user_id)
        if cv is None:
            raise NoCvUploaded("no CV has been uploaded yet")
        revision = await self._documents.latest(EntityKind.CANDIDATE, uuid.UUID(cv.id))
        if revision is None:
            return []
        return await self._profiles.history(uuid.UUID(revision.id))

    async def _current_pair(self, user_id: uuid.UUID) -> tuple[str, ProfileRevision]:
        cv = await self._candidates.get_active_cv(user_id)
        if cv is None:
            raise NoCvUploaded("no CV has been uploaded yet")

        revision = await self._documents.latest(EntityKind.CANDIDATE, uuid.UUID(cv.id))
        if revision is None:
            raise NotExtractedYet("this CV has no stored revision yet")

        profile_revision = await self._profiles.current(uuid.UUID(revision.id))
        if profile_revision is None:
            raise NotExtractedYet("this CV has not been extracted yet")
        return revision.id, profile_revision


def _reviewed(revision: ProfileRevision) -> bool:
    quality = revision.extracted_profile.get("quality", {})
    return bool(quality.get("user_reviewed", False)) if isinstance(quality, dict) else False
