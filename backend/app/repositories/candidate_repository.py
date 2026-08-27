"""Persistence for CandidateProfile / UserPreference."""

from app.domain.candidates.models import CandidateProfile, UserPreference


class CandidateRepository:
    async def get_profile(self, user_id: str) -> CandidateProfile | None:
        raise NotImplementedError

    async def save_profile(self, profile: CandidateProfile) -> CandidateProfile:
        raise NotImplementedError

    async def get_preferences(self, user_id: str) -> UserPreference | None:
        raise NotImplementedError

    async def save_preferences(self, preferences: UserPreference) -> UserPreference:
        raise NotImplementedError
