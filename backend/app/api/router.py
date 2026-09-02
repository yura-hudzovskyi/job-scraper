from fastapi import APIRouter

from app.api.routes import auth, cv, jobs, profile, settings, sources, system, telegram

api_router = APIRouter()

for module in (auth, cv, profile, jobs, sources, settings, telegram, system):
    api_router.include_router(module.router)
