"""The pipeline, start to finish, as one task.

    scrape  ->  embed  ->  match  ->  notify

It is one task rather than a chain of four because the steps are strictly
sequential, each one is only worth running if the last one worked, and a single
task is the only shape where "what is the pipeline doing right now" has one
honest answer. Every step appends its own counts to a `pipeline_runs` row as it
finishes, so the System page shows real progress and a finished run explains
itself — "scraped 40, embedded 40, matched 0 because no CV is uploaded" is a very
different outcome from "nothing happened", and both are visible.

The same task backs the scheduled tick and the System page's "Run pipeline now"
button. There is deliberately no second code path for the manual case: the button
runs exactly what the schedule runs.
"""

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from app.config.settings import get_settings
from app.db.session import session_scope
from app.domain.matching.filters import HardFilterService
from app.domain.pipeline_config import PipelineConfig
from app.integrations.sources.base import JobSearchCriteria
from app.integrations.sources.categories import CATEGORIES_BY_SOURCE
from app.integrations.sources.registry import build_default_registry
from app.integrations.voyage import VoyageClient
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.pipeline_config_repository import PipelineConfigRepository
from app.repositories.pipeline_run_repository import (
    FAILED,
    SUCCEEDED,
    PipelineRunRepository,
)
from app.services.embedding_service import EmbeddingService
from app.services.job_ingestion_service import JobIngestionService
from app.services.matching_service import MatchingService
from app.workers.celery_app import celery_app
from app.workers.tasks.notify import dispatch_match

logger = logging.getLogger(__name__)


class VoyageNotConfigured(RuntimeError):
    """Raised before anything is attempted: with no API key there is neither
    embedding search nor reranking, so the run has nothing to do and says so
    instead of half-finishing."""


async def _voyage() -> VoyageClient:
    settings = get_settings()
    if not settings.voyage_api_key:
        raise VoyageNotConfigured("VOYAGE_API_KEY is not set")
    async with session_scope() as session:
        config = await PipelineConfigRepository(session).get()
    return VoyageClient(
        settings.voyage_api_key,
        embedding_model=config.embedding_model,
        rerank_model=config.rerank_model,
    )


async def _config() -> PipelineConfig:
    async with session_scope() as session:
        return await PipelineConfigRepository(session).get()


# --- steps -------------------------------------------------------------------


async def _scrape_all() -> dict[str, Any]:
    """One category per source per run — whichever has gone longest without one.
    A source that fails is recorded and the others carry on; one broken parser is
    not a broken pipeline."""
    config = await _config()
    if not config.scrape_enabled:
        return {"status": "skipped", "reason": "scraping is turned off in the pipeline config"}

    registry = build_default_registry()
    per_source: list[dict[str, Any]] = []
    for adapter in registry.all():
        source = adapter.source_name
        categories = CATEGORIES_BY_SOURCE[source]
        started_at = datetime.now(UTC)

        async with session_scope() as session:
            category = await JobRepository(session).get_least_recently_scraped_category(
                source, categories
            )

        seen, new, errors, error_text = 0, 0, 0, None
        try:
            async with session_scope() as session:
                result = await JobIngestionService(JobRepository(session)).ingest_source(
                    adapter,
                    JobSearchCriteria(keywords=[category]),
                    max_jobs=config.scrape_max_jobs_per_run,
                )
            seen, new = result.jobs_seen, result.jobs_processed
        except Exception as exc:
            errors, error_text = 1, str(exc)
            logger.warning("scraping %s (%s) failed", source, category, exc_info=True)

        async with session_scope() as session:
            await JobRepository(session).record_scrape_run(
                source=source,
                category=category,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                jobs_seen=seen,
                new_count=new,
                errors=errors,
            )

        entry: dict[str, Any] = {
            "source": source,
            "category": category,
            "seen": seen,
            "new": new,
        }
        if error_text:
            entry["error"] = error_text
        per_source.append(entry)

    return {
        "status": "ok",
        "sources": per_source,
        "new": sum(int(entry["new"]) for entry in per_source),
    }


async def _embed_all() -> dict[str, Any]:
    voyage = await _voyage()
    async with session_scope() as session:
        service = EmbeddingService(
            EmbeddingRepository(session), JobRepository(session), voyage
        )
        result = await service.index_jobs()
    return {"status": "ok" if result.failed == 0 else "partial", **asdict(result)}


async def _match_all() -> dict[str, Any]:
    """Every user with a CV gets a fresh pass. Returns which (user, job) pairs are
    worth a notification — dispatch itself happens outside the DB session so a
    slow Telegram call never holds a transaction open."""
    voyage = await _voyage()
    config = await _config()

    async with session_scope() as session:
        user_ids = await CandidateRepository(session).list_user_ids_with_cv()
    if not user_ids:
        return {"status": "skipped", "reason": "no user has uploaded a CV yet", "users": 0}

    per_user: list[dict[str, Any]] = []
    notify: list[tuple[str, str]] = []
    filters = HardFilterService()
    for user_id in user_ids:
        async with session_scope() as session:
            service = MatchingService(
                config,
                voyage,
                CandidateRepository(session),
                JobRepository(session),
                EmbeddingRepository(session),
                MatchRepository(session),
                filters,
            )
            result = await service.run_for_user(user_id)
        per_user.append(
            {
                "user_id": result.user_id,
                "skipped_reason": result.skipped_reason,
                "retrieved": result.retrieved,
                "eligible": result.eligible,
                "filtered_out": result.filtered_out,
                "reranked": result.reranked,
                "rerank_failed": result.rerank_failed,
                "written": result.written,
                "recommendations": result.recommendations,
            }
        )
        notify.extend((result.user_id, job_id) for job_id in result.notify)

    return {
        "status": "ok",
        "users": len(user_ids),
        "results": per_user,
        "notify": [list(pair) for pair in notify],
    }


# --- the run -----------------------------------------------------------------


async def _run(trigger: str, steps: tuple[str, ...]) -> dict[str, Any]:
    async with session_scope() as session:
        run_id = await PipelineRunRepository(session).start(trigger)

    async def record(name: str, detail: dict[str, Any]) -> None:
        async with session_scope() as session:
            await PipelineRunRepository(session).add_step(run_id, name, detail)

    notify: list[list[str]] = []
    try:
        if "scrape" in steps:
            await record("scrape", await _scrape_all())
        if "embed" in steps:
            await record("embed", await _embed_all())
        if "match" in steps:
            detail = await _match_all()
            notify = detail.pop("notify", [])
            await record("match", detail)
    except Exception as exc:
        async with session_scope() as session:
            await PipelineRunRepository(session).finish(run_id, FAILED, error=str(exc))
        raise

    for user_id, canonical_job_id in notify:
        dispatch_match.delay(user_id, canonical_job_id)
    if "match" in steps:
        await record("notify", {"status": "ok", "queued": len(notify)})

    async with session_scope() as session:
        await PipelineRunRepository(session).finish(run_id, SUCCEEDED)
    return {"run_id": str(run_id), "notifications_queued": len(notify)}


@celery_app.task(name="pipeline.run_full")
def run_full() -> dict[str, Any]:
    """Scrape, embed, match, notify. The scheduled tick and the System page's
    button both call exactly this."""
    return asyncio.run(_run("full", ("scrape", "embed", "match")))


@celery_app.task(name="pipeline.run_matching")
def run_matching() -> dict[str, Any]:
    """Everything except scraping — what to run after changing a CV, preferences
    or the matching config, when the corpus itself is already current."""
    return asyncio.run(_run("match", ("embed", "match")))


@celery_app.task(name="pipeline.run_scrape")
def run_scrape() -> dict[str, Any]:
    """Fetch new vacancies only, without re-matching. Mostly useful when
    debugging a source adapter."""
    return asyncio.run(_run("scrape", ("scrape",)))


async def _run_for_user(user_id: str) -> dict[str, Any]:
    voyage = await _voyage()
    config = await _config()
    async with session_scope() as session:
        service = MatchingService(
            config,
            voyage,
            CandidateRepository(session),
            JobRepository(session),
            EmbeddingRepository(session),
            MatchRepository(session),
        )
        result = await service.run_for_user(uuid.UUID(user_id))
    for canonical_job_id in result.notify:
        dispatch_match.delay(result.user_id, canonical_job_id)
    return {
        "written": result.written,
        "skipped_reason": result.skipped_reason,
        "notifications_queued": len(result.notify),
    }


@celery_app.task(name="pipeline.match_user")
def match_user(user_id: str) -> dict[str, Any]:
    """One user's matching pass — fired when that user changes their CV or
    preferences, so they don't wait for the next scheduled run. Assumes the
    corpus is already embedded; the full run is what fills it."""
    return asyncio.run(_run_for_user(user_id))
