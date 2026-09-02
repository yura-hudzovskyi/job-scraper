"""FastAPI dependency wiring: constructs repositories and services per request.

get_current_user_id verifies a JWT bearer token (app/security/tokens.py) rather
than touching the database — auth is stateless, no session lookup needed.
"""

import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.session import get_session
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.pipeline_config_repository import PipelineConfigRepository
from app.repositories.pipeline_run_repository import PipelineRunRepository
from app.repositories.user_repository import UserRepository
from app.security.tokens import InvalidToken, decode_access_token
from app.services.auth_service import AuthService
from app.services.cv_service import CvService
from app.services.job_ingestion_service import JobIngestionService
from app.services.job_service import JobService
from app.services.system_service import SystemService

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> uuid.UUID:
    if credentials is None:
        raise HTTPException(
            status_code=401, detail="not authenticated", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        return decode_access_token(credentials.credentials, get_settings().secret_key)
    except InvalidToken as exc:
        raise HTTPException(
            status_code=401,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_user_repository(session: AsyncSession = Depends(get_session)) -> UserRepository:
    return UserRepository(session)


def get_candidate_repository(session: AsyncSession = Depends(get_session)) -> CandidateRepository:
    return CandidateRepository(session)


def get_embedding_repository(session: AsyncSession = Depends(get_session)) -> EmbeddingRepository:
    return EmbeddingRepository(session)


def get_job_repository(session: AsyncSession = Depends(get_session)) -> JobRepository:
    return JobRepository(session)


def get_match_repository(session: AsyncSession = Depends(get_session)) -> MatchRepository:
    return MatchRepository(session)


def get_notification_repository(
    session: AsyncSession = Depends(get_session),
) -> NotificationRepository:
    return NotificationRepository(session)


def get_pipeline_config_repository(
    session: AsyncSession = Depends(get_session),
) -> PipelineConfigRepository:
    return PipelineConfigRepository(session)


def get_pipeline_run_repository(
    session: AsyncSession = Depends(get_session),
) -> PipelineRunRepository:
    return PipelineRunRepository(session)


def get_auth_service(
    user_repository: UserRepository = Depends(get_user_repository),
) -> AuthService:
    return AuthService(user_repository, get_settings().secret_key)


def get_cv_service(
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
) -> CvService:
    return CvService(candidate_repository)


def get_job_service(
    job_repository: JobRepository = Depends(get_job_repository),
    match_repository: MatchRepository = Depends(get_match_repository),
) -> JobService:
    return JobService(job_repository, match_repository)


def get_job_ingestion_service(
    job_repository: JobRepository = Depends(get_job_repository),
) -> JobIngestionService:
    return JobIngestionService(job_repository)


def get_system_service(
    job_repository: JobRepository = Depends(get_job_repository),
    match_repository: MatchRepository = Depends(get_match_repository),
    notification_repository: NotificationRepository = Depends(get_notification_repository),
    embedding_repository: EmbeddingRepository = Depends(get_embedding_repository),
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
    run_repository: PipelineRunRepository = Depends(get_pipeline_run_repository),
) -> SystemService:
    return SystemService(
        job_repository,
        match_repository,
        notification_repository,
        embedding_repository,
        candidate_repository,
        run_repository,
    )
