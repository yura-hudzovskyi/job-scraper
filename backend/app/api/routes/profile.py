"""Onboarding summary — what the Dashboard checks to tell the user what's left."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_candidate_repository, get_current_user_id
from app.repositories.candidate_repository import CandidateRepository

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileSummaryResponse(BaseModel):
    user_id: str
    cv_count: int
    has_preferences: bool


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
