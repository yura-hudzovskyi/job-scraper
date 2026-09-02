from fastapi import APIRouter

from app.api.routes import (
    ai_settings,
    applications,
    auth,
    cv,
    jobs,
    matches,
    pipeline,
    profile,
    search_profiles,
    settings,
    sources,
    system,
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
    ai_settings,
    pipeline,
    system,
):
    api_router.include_router(module.router)
