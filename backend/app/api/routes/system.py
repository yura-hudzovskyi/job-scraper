"""Ops actions for the small self-hosted stack this app runs on — Redis (the app
cache: circuit-breaker cooldowns, LlmReranker's daily call budget, AI model
overrides — see app/repositories/ai_settings_repository.py) and Celery's broker
queue. Both hold only transient/derived state, never a system of record (that's
Postgres) — clearing either is always safe to retry and never loses user data.
"""

import asyncio
import uuid

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user_id
from app.config.settings import get_settings
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/api/system", tags=["system"])


class FlushRedisResponse(BaseModel):
    databases_flushed: int


@router.post("/redis/flush", response_model=FlushRedisResponse)
async def flush_redis(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> FlushRedisResponse:
    """Flushes every logical Redis database this app uses — REDIS_URL (circuit
    breakers, the daily rerank budget, AI model overrides above) *and* Celery's
    own CELERY_BROKER_URL/CELERY_RESULT_BACKEND. This also reverts any AI model
    override set via the System page back to its .env default, and discards
    stored Celery task results — see /celery/purge below for a narrower action
    that only drops not-yet-started queued tasks."""
    settings = get_settings()
    urls = {settings.redis_url, settings.celery_broker_url, settings.celery_result_backend}
    for url in urls:
        client = redis.from_url(url)
        await client.flushdb()
    return FlushRedisResponse(databases_flushed=len(urls))


class PurgeCeleryResponse(BaseModel):
    purged: int


@router.post("/celery/purge", response_model=PurgeCeleryResponse)
async def purge_celery(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> PurgeCeleryResponse:
    """Discards every task still waiting in the broker queue that no worker has
    picked up yet (celery_app.control.purge()) — the standard fix for a backlog
    stuck behind e.g. a bad scrape or a "rescore all vacancies" fan-out gone
    wrong. Does not touch a task a worker has already started. Run off the event
    loop since Celery's control API is synchronous."""
    purged = await asyncio.to_thread(celery_app.control.purge)
    return PurgeCeleryResponse(purged=purged or 0)
