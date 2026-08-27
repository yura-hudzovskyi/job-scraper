"""Persistence for CV documents and user preferences.

CandidateProfile (skills/experience derived from a CV via an LLM) isn't persisted yet —
that's Phase 2, once an LLMProvider exists. See docs/roadmap.md.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.candidate import CvDocumentModel, UserPreferenceModel
from app.domain.candidates.models import CvDocument, UserPreference


def _to_cv_document(model: CvDocumentModel) -> CvDocument:
    return CvDocument(
        id=str(model.id),
        user_id=str(model.user_id),
        filename=model.filename,
        raw_text=model.raw_text,
        uploaded_at=model.uploaded_at,
    )


def _to_user_preference(model: UserPreferenceModel) -> UserPreference:
    return UserPreference(
        user_id=str(model.user_id),
        desired_salary_usd=model.desired_salary_usd,
        preferred_roles=list(model.preferred_roles),
        preferred_stack=list(model.preferred_stack),
        acceptable_stack=list(model.acceptable_stack),
        blocked_stack=list(model.blocked_stack),
        work_formats=list(model.work_formats),
        locations=list(model.locations),
        max_required_experience=model.max_required_experience,
        industries_blacklist=list(model.industries_blacklist),
        companies_blacklist=list(model.companies_blacklist),
    )


class CandidateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save_cv_document(self, user_id: uuid.UUID, filename: str, raw_text: str) -> CvDocument:
        model = CvDocumentModel(user_id=user_id, filename=filename, raw_text=raw_text)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_cv_document(model)

    async def list_cv_documents(self, user_id: uuid.UUID) -> list[CvDocument]:
        result = await self._session.execute(
            select(CvDocumentModel)
            .where(CvDocumentModel.user_id == user_id)
            .order_by(CvDocumentModel.uploaded_at.desc())
        )
        return [_to_cv_document(model) for model in result.scalars()]

    async def get_preferences(self, user_id: uuid.UUID) -> UserPreference | None:
        result = await self._session.execute(
            select(UserPreferenceModel).where(UserPreferenceModel.user_id == user_id)
        )
        model = result.scalar_one_or_none()
        return _to_user_preference(model) if model else None

    async def save_preferences(
        self, user_id: uuid.UUID, preferences: UserPreference
    ) -> UserPreference:
        result = await self._session.execute(
            select(UserPreferenceModel).where(UserPreferenceModel.user_id == user_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            model = UserPreferenceModel(user_id=user_id)
            self._session.add(model)

        model.desired_salary_usd = preferences.desired_salary_usd
        model.preferred_roles = preferences.preferred_roles
        model.preferred_stack = preferences.preferred_stack
        model.acceptable_stack = preferences.acceptable_stack
        model.blocked_stack = preferences.blocked_stack
        model.work_formats = preferences.work_formats
        model.locations = preferences.locations
        model.max_required_experience = preferences.max_required_experience
        model.industries_blacklist = preferences.industries_blacklist
        model.companies_blacklist = preferences.companies_blacklist

        await self._session.flush()
        await self._session.refresh(model)
        return _to_user_preference(model)
