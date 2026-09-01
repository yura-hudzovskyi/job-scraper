"""Persistence for CV documents, LLM-extracted candidate profiles, and preferences."""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.candidate import CandidateProfileModel, CvDocumentModel, UserPreferenceModel
from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    CvDocument,
    ExperienceEntry,
    SkillLevel,
    UserPreference,
)
from app.domain.versioning import profile_content_hash


def _to_cv_document(model: CvDocumentModel) -> CvDocument:
    return CvDocument(
        id=str(model.id),
        user_id=str(model.user_id),
        filename=model.filename,
        raw_text=model.raw_text,
        uploaded_at=model.uploaded_at,
    )


def _to_candidate_profile(model: CandidateProfileModel) -> CandidateProfile:
    return CandidateProfile(
        id=str(model.id),
        user_id=str(model.user_id),
        experience_years=model.experience_years,
        roles=list(model.roles),
        skills=[
            CandidateSkill(name=skill["name"], level=SkillLevel(skill["level"]), years=skill.get("years"))
            for skill in model.skills
        ],
        experience=[
            ExperienceEntry(
                company=entry["company"],
                title=entry["title"],
                start_date=entry["start_date"],
                end_date=entry.get("end_date"),
                description=entry["description"],
                skills=list(entry.get("skills", [])),
            )
            for entry in model.experience
        ],
        achievements=list(model.achievements),
        domains=list(model.domains),
        ai_experience=list(model.ai_experience),
        generated_by=model.generated_by,
        version=model.version,
        content_hash=model.content_hash,
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

    async def get_cv_document(
        self, user_id: uuid.UUID, cv_document_id: uuid.UUID
    ) -> CvDocument | None:
        result = await self._session.execute(
            select(CvDocumentModel).where(
                CvDocumentModel.id == cv_document_id, CvDocumentModel.user_id == user_id
            )
        )
        model = result.scalar_one_or_none()
        return _to_cv_document(model) if model else None

    async def delete_cv_document(self, user_id: uuid.UUID, cv_document_id: uuid.UUID) -> bool:
        """Returns False when there was no such CV for this user (nothing deleted) —
        callers turn that into a 404. Any CandidateProfile already extracted from
        this CV survives (cv_document_id ON DELETE SET NULL, see
        app/db/models/candidate.py) since it's a self-contained snapshot, not a live
        view of the CV text."""
        result = await self._session.execute(
            delete(CvDocumentModel).where(
                CvDocumentModel.id == cv_document_id, CvDocumentModel.user_id == user_id
            )
        )
        await self._session.flush()
        return result.rowcount > 0

    async def save_candidate_profile(
        self, user_id: uuid.UUID, cv_document_id: uuid.UUID, profile: CandidateProfile
    ) -> CandidateProfile:
        """Each analysis creates a new snapshot rather than overwriting — re-running
        analyze_cv keeps history instead of silently discarding the previous read.
        Each snapshot gets the next version number for this user and a hash of its
        own content, so a match scored against it stays attributable to exactly
        this revision of the CV (see app/domain/versioning.py)."""
        version_result = await self._session.execute(
            select(func.coalesce(func.max(CandidateProfileModel.version), 0) + 1).where(
                CandidateProfileModel.user_id == user_id
            )
        )
        model = CandidateProfileModel(
            version=version_result.scalar_one(),
            content_hash=profile_content_hash(profile),
            user_id=user_id,
            cv_document_id=cv_document_id,
            experience_years=profile.experience_years,
            roles=profile.roles,
            skills=[
                {"name": skill.name, "level": skill.level.value, "years": skill.years}
                for skill in profile.skills
            ],
            experience=[
                {
                    "company": entry.company,
                    "title": entry.title,
                    "start_date": entry.start_date,
                    "end_date": entry.end_date,
                    "description": entry.description,
                    "skills": entry.skills,
                }
                for entry in profile.experience
            ],
            achievements=profile.achievements,
            domains=profile.domains,
            ai_experience=profile.ai_experience,
            generated_by=profile.generated_by,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_candidate_profile(model)

    async def get_latest_candidate_profile(self, user_id: uuid.UUID) -> CandidateProfile | None:
        result = await self._session.execute(
            select(CandidateProfileModel)
            .where(CandidateProfileModel.user_id == user_id)
            .order_by(CandidateProfileModel.extracted_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return _to_candidate_profile(model) if model else None

    async def list_user_ids_with_profile(self) -> list[uuid.UUID]:
        """Users who've finished onboarding (analyzed at least one CV) — score_job_for_user
        hard-requires a CandidateProfile, so this is the fan-out gate in scrape.py."""
        result = await self._session.execute(select(CandidateProfileModel.user_id).distinct())
        return list(result.scalars())

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
