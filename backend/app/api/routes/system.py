"""Everything the System page needs: what the pipeline is, what state it's in,
how to change it, how to run it, and how to wipe it.

One router on purpose. The old split (AI models here, Redis ops there, pipeline
flags in .env) meant no single screen could tell you why nothing was happening.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import (
    get_current_user_id,
    get_pipeline_config_repository,
    get_pipeline_run_repository,
    get_system_service,
)
from app.config.settings import get_settings
from app.domain import pipeline_config as config_meta
from app.domain.pipeline_config import PipelineConfig
from app.integrations.sources.categories import CATEGORIES_BY_SOURCE
from app.integrations.voyage import VoyageClient
from app.repositories.pipeline_config_repository import PipelineConfigRepository
from app.repositories.pipeline_run_repository import PipelineRun, PipelineRunRepository
from app.services.system_service import SystemService
from app.workers.celery_app import celery_app
from app.workers.tasks import pipeline as pipeline_tasks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


# --- shapes ------------------------------------------------------------------


class ConfigField(BaseModel):
    """One editable setting, with everything the UI needs to render and explain
    it. The description travels with the value so the form can never document a
    setting differently from the code that uses it."""

    name: str
    value: Any
    default: Any
    type: str
    description: str
    minimum: float | None = None
    maximum: float | None = None


class PipelineConfigResponse(BaseModel):
    fields: list[ConfigField]


class EmbeddingStatusResponse(BaseModel):
    model: str
    jobs_embedded: int
    jobs_total: int
    profiles_embedded: int
    stale_vectors: int


class PipelineRunResponse(BaseModel):
    id: str
    trigger: str
    status: str
    steps: list[dict[str, Any]]
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class SystemStatusResponse(BaseModel):
    ready: bool
    blockers: list[str]
    voyage_configured: bool
    telegram_configured: bool
    scrape_interval_seconds: int
    sources: dict[str, int]
    categories: dict[str, list[str]]
    counts: dict[str, int]
    embeddings: EmbeddingStatusResponse
    config: PipelineConfigResponse
    active_run: PipelineRunResponse | None
    recent_runs: list[PipelineRunResponse]


def _to_run_response(run: PipelineRun) -> PipelineRunResponse:
    return PipelineRunResponse(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        steps=run.steps,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _to_config_response(config: PipelineConfig) -> PipelineConfigResponse:
    defaults = config_meta.DEFAULTS.as_dict()
    fields = []
    for name, value in config.as_dict().items():
        minimum, maximum = config_meta.BOUNDS.get(name, (None, None))
        fields.append(
            ConfigField(
                name=name,
                value=value,
                default=defaults[name],
                type=type(value).__name__,
                description=config_meta.DESCRIPTIONS.get(name, ""),
                minimum=minimum,
                maximum=maximum,
            )
        )
    return PipelineConfigResponse(fields=fields)


# --- status ------------------------------------------------------------------


@router.get("/status", response_model=SystemStatusResponse)
async def get_status(
    user_id: uuid.UUID = Depends(get_current_user_id),
    system_service: SystemService = Depends(get_system_service),
    config_repository: PipelineConfigRepository = Depends(get_pipeline_config_repository),
) -> SystemStatusResponse:
    settings = get_settings()
    config = await config_repository.get()
    status = await system_service.status(
        config,
        voyage_configured=bool(settings.voyage_api_key),
        telegram_configured=bool(settings.telegram_bot_token),
        scrape_interval_seconds=settings.scrape_interval_seconds,
    )
    return SystemStatusResponse(
        ready=status.ready,
        blockers=status.blockers,
        voyage_configured=status.voyage_configured,
        telegram_configured=status.telegram_configured,
        scrape_interval_seconds=status.scrape_interval_seconds,
        sources=status.sources,
        categories={source: list(cats) for source, cats in CATEGORIES_BY_SOURCE.items()},
        counts=status.counts,
        embeddings=EmbeddingStatusResponse(**vars(status.embeddings)),
        config=_to_config_response(status.config),
        active_run=_to_run_response(status.active_run) if status.active_run else None,
        recent_runs=[_to_run_response(run) for run in status.recent_runs],
    )


# --- config ------------------------------------------------------------------


@router.get("/config", response_model=PipelineConfigResponse)
async def get_config(
    user_id: uuid.UUID = Depends(get_current_user_id),
    config_repository: PipelineConfigRepository = Depends(get_pipeline_config_repository),
) -> PipelineConfigResponse:
    return _to_config_response(await config_repository.get())


class ConfigUpdateRequest(BaseModel):
    """A partial update: only the named fields change. Unknown names are rejected
    rather than ignored, so a typo in a field name fails visibly instead of
    silently doing nothing."""

    values: dict[str, Any]


@router.patch("/config", response_model=PipelineConfigResponse)
async def update_config(
    payload: ConfigUpdateRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    config_repository: PipelineConfigRepository = Depends(get_pipeline_config_repository),
) -> PipelineConfigResponse:
    current = await config_repository.get()
    known = current.as_dict()

    changes: dict[str, Any] = {}
    for name, value in payload.values.items():
        if name not in known:
            raise HTTPException(status_code=422, detail=f"unknown setting: {name}")
        expected = type(known[name])
        try:
            coerced = expected(value) if not isinstance(value, expected) else value
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"{name} must be a {expected.__name__}"
            ) from exc
        if name in config_meta.BOUNDS:
            minimum, maximum = config_meta.BOUNDS[name]
            if not minimum <= float(coerced) <= maximum:
                raise HTTPException(
                    status_code=422,
                    detail=f"{name} must be between {minimum:g} and {maximum:g}",
                )
        if isinstance(coerced, str) and not coerced.strip():
            raise HTTPException(status_code=422, detail=f"{name} cannot be blank")
        changes[name] = coerced

    if changes.get("consider_threshold", current.consider_threshold) > changes.get(
        "apply_threshold", current.apply_threshold
    ):
        raise HTTPException(
            status_code=422, detail="consider_threshold must not exceed apply_threshold"
        )

    return _to_config_response(await config_repository.save(current.replace(**changes)))


@router.post("/config/reset", response_model=PipelineConfigResponse)
async def reset_config(
    user_id: uuid.UUID = Depends(get_current_user_id),
    config_repository: PipelineConfigRepository = Depends(get_pipeline_config_repository),
) -> PipelineConfigResponse:
    return _to_config_response(await config_repository.save(config_meta.DEFAULTS))


class TestVoyageResponse(BaseModel):
    embedding_ok: bool
    embedding_dimension: int | None = None
    rerank_ok: bool
    error: str | None = None


@router.post("/config/test", response_model=TestVoyageResponse)
async def test_voyage(
    user_id: uuid.UUID = Depends(get_current_user_id),
    config_repository: PipelineConfigRepository = Depends(get_pipeline_config_repository),
) -> TestVoyageResponse:
    """One tiny real call against each configured model. A mistyped or retired
    model id otherwise only surfaces as an empty jobs list hours later, which is
    the single most confusing failure this app can have."""
    settings = get_settings()
    if not settings.voyage_api_key:
        return TestVoyageResponse(
            embedding_ok=False, rerank_ok=False, error="VOYAGE_API_KEY is not set"
        )
    config = await config_repository.get()
    client = VoyageClient(settings.voyage_api_key, config.embedding_model, config.rerank_model)

    try:
        vectors = await client.embed(["backend engineer, python, postgres"])
    except Exception as exc:
        logger.warning("Voyage embedding test failed", exc_info=True)
        return TestVoyageResponse(
            embedding_ok=False, rerank_ok=False, error=f"{config.embedding_model}: {exc}"
        )

    try:
        await client.rerank("backend engineer", ["python and postgres role"])
    except Exception as exc:
        logger.warning("Voyage rerank test failed", exc_info=True)
        return TestVoyageResponse(
            embedding_ok=True,
            embedding_dimension=len(vectors[0]) if vectors else None,
            rerank_ok=False,
            error=f"{config.rerank_model}: {exc}",
        )

    return TestVoyageResponse(
        embedding_ok=True,
        embedding_dimension=len(vectors[0]) if vectors else None,
        rerank_ok=True,
    )


# --- running -----------------------------------------------------------------


class RunResponse(BaseModel):
    status: str
    task: str


@router.post("/run", response_model=RunResponse)
async def run_pipeline(
    steps: str = "full",
    user_id: uuid.UUID = Depends(get_current_user_id),
    run_repository: PipelineRunRepository = Depends(get_pipeline_run_repository),
) -> RunResponse:
    """`steps` picks how much to run: "full" (scrape, embed, match, notify),
    "match" (embed + match, no scraping), or "scrape" (fetch only). All three go
    through the same task the schedule uses."""
    if await run_repository.active() is not None:
        raise HTTPException(status_code=409, detail="a pipeline run is already in progress")

    tasks = {
        "full": pipeline_tasks.run_full,
        "match": pipeline_tasks.run_matching,
        "scrape": pipeline_tasks.run_scrape,
    }
    task = tasks.get(steps)
    if task is None:
        raise HTTPException(status_code=422, detail=f"unknown steps: {steps}")
    task.delay()
    return RunResponse(status="queued", task=steps)


@router.get("/runs", response_model=list[PipelineRunResponse])
async def list_runs(
    limit: int = 20,
    user_id: uuid.UUID = Depends(get_current_user_id),
    run_repository: PipelineRunRepository = Depends(get_pipeline_run_repository),
) -> list[PipelineRunResponse]:
    runs = await run_repository.latest(max(1, min(limit, 100)))
    return [_to_run_response(run) for run in runs]


# --- resets ------------------------------------------------------------------


class ResetResponse(BaseModel):
    """What was actually deleted, per table. Never a bare "ok" — a destructive
    action should say what it destroyed."""

    deleted: dict[str, int]


@router.post("/reset/notifications", response_model=ResetResponse)
async def reset_notifications(
    user_id: uuid.UUID = Depends(get_current_user_id),
    system_service: SystemService = Depends(get_system_service),
) -> ResetResponse:
    return ResetResponse(deleted=await system_service.reset_notifications())


@router.post("/reset/matches", response_model=ResetResponse)
async def reset_matches(
    user_id: uuid.UUID = Depends(get_current_user_id),
    system_service: SystemService = Depends(get_system_service),
) -> ResetResponse:
    return ResetResponse(deleted=await system_service.reset_matches())


@router.post("/reset/embeddings", response_model=ResetResponse)
async def reset_embeddings(
    user_id: uuid.UUID = Depends(get_current_user_id),
    system_service: SystemService = Depends(get_system_service),
) -> ResetResponse:
    return ResetResponse(deleted=await system_service.reset_embeddings())


@router.post("/reset/jobs", response_model=ResetResponse)
async def reset_jobs(
    user_id: uuid.UUID = Depends(get_current_user_id),
    system_service: SystemService = Depends(get_system_service),
) -> ResetResponse:
    return ResetResponse(deleted=await system_service.reset_jobs())


@router.post("/reset/all", response_model=ResetResponse)
async def reset_all(
    user_id: uuid.UUID = Depends(get_current_user_id),
    system_service: SystemService = Depends(get_system_service),
) -> ResetResponse:
    """Vacancies, vectors, matches, notifications and run history. Keeps the
    account: user, CVs, preferences, Telegram connection, pipeline config."""
    deleted = await system_service.reset_all()
    deleted.update(await _flush_queue())
    return ResetResponse(deleted=deleted)


async def _flush_queue() -> dict[str, int]:
    """Drops tasks nobody has picked up yet. Part of a full reset because a
    queued run from before the wipe would otherwise repopulate half of it."""
    purged = await asyncio.to_thread(celery_app.control.purge)
    return {"queued_tasks": purged or 0}


@router.post("/queue/purge", response_model=ResetResponse)
async def purge_queue(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> ResetResponse:
    """The fix for a backlog stuck behind a bad run. Does not stop a task a
    worker has already started."""
    return ResetResponse(deleted=await _flush_queue())


@router.post("/redis/flush", response_model=ResetResponse)
async def flush_redis(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> ResetResponse:
    """Clears every Redis database this app uses — Celery's broker queue and
    stored task results. Nothing here is a system of record (that's Postgres), so
    it is always safe to retry."""
    settings = get_settings()
    urls = {settings.redis_url, settings.celery_broker_url, settings.celery_result_backend}
    for url in urls:
        client = redis.from_url(url)
        await client.flushdb()
    return ResetResponse(deleted={"redis_databases": len(urls)})
