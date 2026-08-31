from fastapi import APIRouter

from app.api.routes import (
    applications,
    auth,
    cv,
    jobs,
    llm,
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
    llm,
):
    api_router.include_router(module.router)
