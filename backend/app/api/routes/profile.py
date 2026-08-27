"""Basic profile summary — see docs/api.md.

Full CandidateProfile (skills/experience extracted from a CV) doesn't exist until
Phase 2's LLM extraction, so this only reports what's on file. Preferences (what the
candidate wants) are edited via /api/settings, not here — see docs/domain-model.md.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user_id, get_profile_service
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileSummaryResponse(BaseModel):
    user_id: str
    cv_count: int
    has_preferences: bool


@router.get("", response_model=ProfileSummaryResponse)
async def get_profile(
    user_id: uuid.UUID = Depends(get_current_user_id),
    profile_service: ProfileService = Depends(get_profile_service),
) -> ProfileSummaryResponse:
    summary = await profile_service.get_profile_summary(user_id)
    return ProfileSummaryResponse(
        user_id=summary.user_id,
        cv_count=len(summary.cv_documents),
        has_preferences=summary.has_preferences,
    )
