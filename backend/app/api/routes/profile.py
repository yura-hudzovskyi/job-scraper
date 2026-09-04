"""Onboarding summary, and the candidate's review of what was extracted from
their CV.

The review endpoints are not a convenience screen. Spec 3.5.2 makes them one of
three conditions separating this extraction layer from the one removed in
b5569d7: the person a profile describes has to be able to say "that is wrong"
before any of it can influence a score.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import (
    get_candidate_repository,
    get_current_user_id,
    get_profile_review_service,
)
from app.domain.profiles.schemas import CompetencyCategory
from app.repositories.candidate_repository import CandidateRepository
from app.services.profile_review_service import (
    NoCvUploaded,
    NotExtractedYet,
    ProfileReviewService,
    ReviewedCompetency,
)

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileSummaryResponse(BaseModel):
    user_id: str
    cv_count: int
    has_preferences: bool


class CompetencyPayload(BaseModel):
    raw_text: str = Field(min_length=1, max_length=200)
    category: CompetencyCategory = CompetencyCategory.OTHER


class ReviewRequest(BaseModel):
    """The full competency list as the candidate wants it.

    Replaces rather than merges — the screen shows the whole list and the user
    edits it, so something they removed has to disappear. A merge would make
    deletion impossible to express.
    """

    competencies: list[CompetencyPayload] = Field(default_factory=list, max_length=200)


class ExtractedProfileResponse(BaseModel):
    document_revision_id: str
    profile_revision_id: str
    origin: str
    user_reviewed: bool
    profile: dict[str, Any]


class ProfileRevisionSummary(BaseModel):
    id: str
    origin: str
    schema_version: str
    extractor_model_id: str | None
    created_at: str | None


@router.get("", response_model=ProfileSummaryResponse)
async def get_profile(
    user_id: uuid.UUID = Depends(get_current_user_id),
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
) -> ProfileSummaryResponse:
    documents = await candidate_repository.list_cv_documents(user_id)
    preferences = await candidate_repository.get_preferences(user_id)
    return ProfileSummaryResponse(
        user_id=str(user_id),
        cv_count=len(documents),
        has_preferences=preferences is not None,
    )


@router.get("/extracted", response_model=ExtractedProfileResponse)
async def get_extracted_profile(
    user_id: uuid.UUID = Depends(get_current_user_id),
    service: ProfileReviewService = Depends(get_profile_review_service),
) -> ExtractedProfileResponse:
    """What was extracted from the active CV, for the user to check.

    404 rather than an empty profile when there is nothing yet: "no CV" and
    "extracted and found nothing" mean different things to whoever is looking at
    the screen, and the detail says which.
    """
    try:
        view = await service.current(user_id)
    except (NoCvUploaded, NotExtractedYet) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExtractedProfileResponse(
        document_revision_id=view.document_revision_id,
        profile_revision_id=view.profile_revision_id,
        origin=view.origin.value,
        user_reviewed=view.user_reviewed,
        profile=view.profile.model_dump(mode="json"),
    )


@router.post("/extracted/review", response_model=ExtractedProfileResponse)
async def submit_review(
    payload: ReviewRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    service: ProfileReviewService = Depends(get_profile_review_service),
) -> ExtractedProfileResponse:
    """Record the candidate's corrections as a new profile revision.

    Never overwrites: the corrected revision points at the one it corrected, so
    the extraction behind a past match stays readable.
    """
    try:
        view = await service.submit_review(
            user_id,
            [
                ReviewedCompetency(raw_text=item.raw_text, category=item.category)
                for item in payload.competencies
            ],
        )
    except (NoCvUploaded, NotExtractedYet) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExtractedProfileResponse(
        document_revision_id=view.document_revision_id,
        profile_revision_id=view.profile_revision_id,
        origin=view.origin.value,
        user_reviewed=view.user_reviewed,
        profile=view.profile.model_dump(mode="json"),
    )


@router.get("/extracted/revisions", response_model=list[ProfileRevisionSummary])
async def list_profile_revisions(
    user_id: uuid.UUID = Depends(get_current_user_id),
    service: ProfileReviewService = Depends(get_profile_review_service),
) -> list[ProfileRevisionSummary]:
    """The extraction and every correction made to it, oldest first."""
    try:
        revisions = await service.history(user_id)
    except NoCvUploaded as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        ProfileRevisionSummary(
            id=revision.id,
            origin=revision.origin.value,
            schema_version=revision.schema_version,
            extractor_model_id=revision.extractor_model_id,
            created_at=revision.created_at.isoformat() if revision.created_at else None,
        )
        for revision in revisions
    ]
