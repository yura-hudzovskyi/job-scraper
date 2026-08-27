"""Use case: user preferences (fully structured, no AI needed) and a basic profile
summary. Full CandidateProfile extraction from a CV is Phase 2 — see docs/roadmap.md.
"""

import uuid
from dataclasses import dataclass

from app.domain.candidates.models import CvDocument, UserPreference
from app.repositories.candidate_repository import CandidateRepository


@dataclass(frozen=True)
class ProfileSummary:
    user_id: str
    cv_documents: list[CvDocument]
    has_preferences: bool


class ProfileService:
    def __init__(self, candidate_repository: CandidateRepository):
        self._candidate_repository = candidate_repository

    async def get_profile_summary(self, user_id: uuid.UUID) -> ProfileSummary:
        cv_documents = await self._candidate_repository.list_cv_documents(user_id)
        preferences = await self._candidate_repository.get_preferences(user_id)
        return ProfileSummary(
            user_id=str(user_id),
            cv_documents=cv_documents,
            has_preferences=preferences is not None,
        )

    async def get_preferences(self, user_id: uuid.UUID) -> UserPreference | None:
        return await self._candidate_repository.get_preferences(user_id)

    async def update_preferences(
        self, user_id: uuid.UUID, preferences: UserPreference
    ) -> UserPreference:
        return await self._candidate_repository.save_preferences(user_id, preferences)
