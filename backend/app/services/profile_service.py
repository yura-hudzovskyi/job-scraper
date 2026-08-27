"""Use case: read/update CandidateProfile and UserPreference."""

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.repositories.candidate_repository import CandidateRepository


class ProfileService:
    def __init__(self, candidate_repository: CandidateRepository):
        self._candidate_repository = candidate_repository

    async def get_profile(self, user_id: str) -> CandidateProfile | None:
        return await self._candidate_repository.get_profile(user_id)

    async def update_preferences(
        self, user_id: str, preferences: UserPreference
    ) -> UserPreference:
        return await self._candidate_repository.save_preferences(preferences)
