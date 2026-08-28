"""FastAPI dependency wiring: constructs services from repositories/integrations.

Routes depend on these, never on repositories or integrations directly.

Phase 1 is single-user, so there's no auth yet — get_current_user_id lazily creates
one default user instead of building real auth, which isn't on the roadmap for a
personal tool.
"""

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.session import get_session
from app.integrations.ai.llm.base import LLMProvider
from app.integrations.ai.llm.factory import build_llm_provider
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository
from app.services.cv_service import CvService
from app.services.default_user import get_or_create_default_user_id
from app.services.job_ingestion_service import JobIngestionService
from app.services.job_service import JobService
from app.services.profile_service import ProfileService


async def get_current_user_id(session: AsyncSession = Depends(get_session)) -> uuid.UUID:
    return await get_or_create_default_user_id(session)


def get_candidate_repository(session: AsyncSession = Depends(get_session)) -> CandidateRepository:
    return CandidateRepository(session)


def get_job_repository(session: AsyncSession = Depends(get_session)) -> JobRepository:
    return JobRepository(session)


def get_match_repository(session: AsyncSession = Depends(get_session)) -> MatchRepository:
    return MatchRepository(session)


def get_notification_repository(
    session: AsyncSession = Depends(get_session),
) -> NotificationRepository:
    return NotificationRepository(session)


def get_llm_provider() -> LLMProvider | None:
    return build_llm_provider(get_settings())


def get_cv_service(
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
    llm_provider: LLMProvider | None = Depends(get_llm_provider),
) -> CvService:
    return CvService(candidate_repository, llm_provider)


def get_profile_service(
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
) -> ProfileService:
    return ProfileService(candidate_repository)


def get_job_service(job_repository: JobRepository = Depends(get_job_repository)) -> JobService:
    return JobService(job_repository)


def get_job_ingestion_service(
    job_repository: JobRepository = Depends(get_job_repository),
) -> JobIngestionService:
    return JobIngestionService(job_repository)
