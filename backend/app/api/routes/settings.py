"""User preferences — what the candidate wants, fully structured, no AI needed.
See docs/domain-model.md. Served at /api/settings per docs/api.md."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_id, get_profile_service
from app.domain.candidates.models import UserPreference
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PreferencesPayload(BaseModel):
    desired_salary_usd: int | None = None
    preferred_roles: list[str] = Field(default_factory=list)
    preferred_stack: list[str] = Field(default_factory=list)
    acceptable_stack: list[str] = Field(default_factory=list)
    blocked_stack: list[str] = Field(default_factory=list)
    work_formats: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    max_required_experience: float | None = None
    industries_blacklist: list[str] = Field(default_factory=list)
    companies_blacklist: list[str] = Field(default_factory=list)


def _to_payload(preferences: UserPreference) -> PreferencesPayload:
    return PreferencesPayload(
        desired_salary_usd=preferences.desired_salary_usd,
        preferred_roles=preferences.preferred_roles,
        preferred_stack=preferences.preferred_stack,
        acceptable_stack=preferences.acceptable_stack,
        blocked_stack=preferences.blocked_stack,
        work_formats=preferences.work_formats,
        locations=preferences.locations,
        max_required_experience=preferences.max_required_experience,
        industries_blacklist=preferences.industries_blacklist,
        companies_blacklist=preferences.companies_blacklist,
    )


@router.get("", response_model=PreferencesPayload | None)
async def get_settings_view(
    user_id: uuid.UUID = Depends(get_current_user_id),
    profile_service: ProfileService = Depends(get_profile_service),
) -> PreferencesPayload | None:
    preferences = await profile_service.get_preferences(user_id)
    return _to_payload(preferences) if preferences else None


@router.patch("", response_model=PreferencesPayload)
async def update_settings_view(
    payload: PreferencesPayload,
    user_id: uuid.UUID = Depends(get_current_user_id),
    profile_service: ProfileService = Depends(get_profile_service),
) -> PreferencesPayload:
    preferences = UserPreference(user_id=str(user_id), **payload.model_dump())
    updated = await profile_service.update_preferences(user_id, preferences)
    return _to_payload(updated)
