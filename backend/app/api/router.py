from fastapi import APIRouter

from app.api.routes import (
    auth,
    cv,
    evaluation,
    jobs,
    profile,
    settings,
    sources,
    system,
    telegram,
)

api_router = APIRouter()

for module in (auth, cv, profile, jobs, sources, settings, telegram, system, evaluation):
    api_router.include_router(module.router)
