"""FastAPI dependency wiring: constructs services from repositories/integrations.

Routes depend on these, never on repositories or integrations directly.
"""

from app.services.job_service import JobService
from app.services.profile_service import ProfileService


def get_profile_service() -> ProfileService:
    raise NotImplementedError


def get_job_service() -> JobService:
    raise NotImplementedError
