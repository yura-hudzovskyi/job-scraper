"""Persistence for uploaded CVs and user preferences."""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.candidate import CvDocumentModel, UserPreferenceModel
from app.domain.candidates.models import CvDocument, UserPreference
from app.repositories.base import rows_affected


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
        blocked_stack=list(model.blocked_stack),
        work_formats=list(model.work_formats),
        locations=list(model.locations),
        max_required_experience=model.max_required_experience,
        companies_blacklist=list(model.companies_blacklist),
    )


class CandidateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save_cv_document(
        self, user_id: uuid.UUID, filename: str, raw_text: str
    ) -> CvDocument:
        model = CvDocumentModel(user_id=user_id, filename=filename, raw_text=raw_text)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_cv_document(model)

    async def list_cv_documents(self, user_id: uuid.UUID) -> list[CvDocument]:
        """Newest first — the first entry is the active CV, the one that gets
        embedded and handed to the reranker."""
        result = await self._session.execute(
            select(CvDocumentModel)
            .where(CvDocumentModel.user_id == user_id)
            .order_by(CvDocumentModel.uploaded_at.desc())
        )
        return [_to_cv_document(model) for model in result.scalars()]

    async def get_active_cv(self, user_id: uuid.UUID) -> CvDocument | None:
        """The most recently uploaded CV. One rule, no hidden state: upload a new
        one to switch, delete it to go back."""
        result = await self._session.execute(
            select(CvDocumentModel)
            .where(CvDocumentModel.user_id == user_id)
            .order_by(CvDocumentModel.uploaded_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return _to_cv_document(model) if model else None

    async def owns_cv_document(self, user_id: uuid.UUID, cv_document_id: uuid.UUID) -> bool:
        """Whether this CV is this user's. Lets a caller clear what hangs off a CV
        before deleting it without having to trust an id from the request."""
        result = await self._session.execute(
            select(CvDocumentModel.id).where(
                CvDocumentModel.id == cv_document_id, CvDocumentModel.user_id == user_id
            )
        )
        return result.scalar_one_or_none() is not None

    async def delete_cv_document(self, user_id: uuid.UUID, cv_document_id: uuid.UUID) -> bool:
        """False when there was no such CV for this user — callers turn that into
        a 404."""
        result = await self._session.execute(
            delete(CvDocumentModel).where(
                CvDocumentModel.id == cv_document_id, CvDocumentModel.user_id == user_id
            )
        )
        await self._session.flush()
        return rows_affected(result) > 0

    async def list_user_ids_with_cv(self) -> list[uuid.UUID]:
        """Everyone who can be matched at all. A CV is the whole candidate side of
        the pipeline, so a user without one is simply skipped."""
        result = await self._session.execute(select(CvDocumentModel.user_id).distinct())
        return list(result.scalars())

    async def count_cvs(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(CvDocumentModel))
        return int(result.scalar_one())

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
        model.blocked_stack = preferences.blocked_stack
        model.work_formats = preferences.work_formats
        model.locations = preferences.locations
        model.max_required_experience = preferences.max_required_experience
        model.companies_blacklist = preferences.companies_blacklist

        await self._session.flush()
        await self._session.refresh(model)
        return _to_user_preference(model)
