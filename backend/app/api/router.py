from fastapi import APIRouter

from app.api.routes import (
    applications,
    auth,
    cv,
    jobs,
    matches,
    profile,
    search_profiles,
    settings,
    sources,
    telegram,
)

api_router = APIRouter()

for module in (
    auth,
    cv,
    profile,
    jobs,
    matches,
    sources,
    search_profiles,
    applications,
    settings,
    telegram,
):
    api_router.include_router(module.router)
