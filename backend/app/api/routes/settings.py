"""User preferences and notification rules — what this one user wants.

Kept separate from /api/system, which is about how the pipeline itself runs.
Preferences drive the hard filters (app/domain/matching/filters.py) and one short
line in the document handed to the models; nothing here is inferred.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import (
    get_candidate_repository,
    get_current_user_id,
    get_notification_repository,
)
from app.domain.candidates.models import UserPreference
from app.domain.notifications.policy import NotificationPolicyConfig
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.notification_repository import NotificationRepository
from app.workers.tasks.pipeline import match_user

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PreferencesPayload(BaseModel):
    desired_salary_usd: int | None = None
    preferred_roles: list[str] = Field(default_factory=list)
    preferred_stack: list[str] = Field(default_factory=list)
    blocked_stack: list[str] = Field(default_factory=list)
    work_formats: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    max_required_experience: float | None = None
    companies_blacklist: list[str] = Field(default_factory=list)


def _to_payload(preferences: UserPreference) -> PreferencesPayload:
    return PreferencesPayload(
        desired_salary_usd=preferences.desired_salary_usd,
        preferred_roles=preferences.preferred_roles,
        preferred_stack=preferences.preferred_stack,
        blocked_stack=preferences.blocked_stack,
        work_formats=preferences.work_formats,
        locations=preferences.locations,
        max_required_experience=preferences.max_required_experience,
        companies_blacklist=preferences.companies_blacklist,
    )


@router.get("", response_model=PreferencesPayload | None)
async def get_preferences(
    user_id: uuid.UUID = Depends(get_current_user_id),
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
) -> PreferencesPayload | None:
    preferences = await candidate_repository.get_preferences(user_id)
    return _to_payload(preferences) if preferences else None


@router.patch("", response_model=PreferencesPayload)
async def update_preferences(
    payload: PreferencesPayload,
    user_id: uuid.UUID = Depends(get_current_user_id),
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
) -> PreferencesPayload:
    """Saving re-matches in the background: preferences change both which
    vacancies pass the filters and the query the models are given, so every
    existing score is stale the moment this returns."""
    updated = await candidate_repository.save_preferences(
        user_id, UserPreference(user_id=str(user_id), **payload.model_dump())
    )
    match_user.delay(str(user_id))
    return _to_payload(updated)


class NotificationSettingsPayload(BaseModel):
    enabled: bool = True
    min_score: float = Field(75.0, ge=0, le=100)
    quiet_hours_start: int = Field(22, ge=0, le=23)
    quiet_hours_end: int = Field(8, ge=0, le=23)


def _to_notification_payload(config: NotificationPolicyConfig) -> NotificationSettingsPayload:
    return NotificationSettingsPayload(
        enabled=config.enabled,
        min_score=config.min_score,
        quiet_hours_start=config.quiet_hours_start,
        quiet_hours_end=config.quiet_hours_end,
    )


@router.get("/notifications", response_model=NotificationSettingsPayload)
async def get_notification_settings(
    user_id: uuid.UUID = Depends(get_current_user_id),
    notification_repository: NotificationRepository = Depends(get_notification_repository),
) -> NotificationSettingsPayload:
    return _to_notification_payload(
        await notification_repository.get_notification_policy_config(user_id)
    )


@router.patch("/notifications", response_model=NotificationSettingsPayload)
async def update_notification_settings(
    payload: NotificationSettingsPayload,
    user_id: uuid.UUID = Depends(get_current_user_id),
    notification_repository: NotificationRepository = Depends(get_notification_repository),
) -> NotificationSettingsPayload:
    saved = await notification_repository.save_notification_policy_config(
        user_id, NotificationPolicyConfig(**payload.model_dump())
    )
    return _to_notification_payload(saved)
