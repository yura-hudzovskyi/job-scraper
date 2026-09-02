"""Keeps the embedding lanes filled: section vectors for one job, and a batch
backfill for everything a lane is still missing — see docs/ai-pipeline-v3.md (C4).

Indexing is gated on MULTI_EMBEDDING_LANES. With the flag off nothing here runs
and matching behaves exactly as before; with it on, jobs are indexed as they are
scraped and a lane only starts serving queries once it covers the corpus.

The per-job task is cheap and idempotent: unchanged sections cost nothing, so
re-running it after any edit to a posting is always safe.
"""

import asyncio
import uuid

from app.config.runtime_settings import get_effective_settings
from app.config.settings import get_settings
from app.db.session import session_scope
from app.integrations.ai.embeddings.lanes import lanes_for
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.services.embedding_indexing_service import EmbeddingIndexingService
from app.workers.celery_app import celery_app

# One backfill pass indexes this many jobs, then re-queues itself. Keeps a first
# run over a full corpus from becoming one enormous transaction — and from
# holding a worker for the whole of it.
_BACKFILL_BATCH = 50


async def _service(session) -> tuple[EmbeddingIndexingService, JobRepository]:
    settings = await get_effective_settings(get_settings())
    job_repository = JobRepository(session)
    service = EmbeddingIndexingService(EmbeddingRepository(session), lanes_for(settings))
    return service, job_repository


async def _run_one(canonical_job_id: str) -> dict[str, int]:
    async with session_scope() as session:
        service, job_repository = await _service(session)
        job = await job_repository.get_normalized_job_for_canonical(uuid.UUID(canonical_job_id))
        if job is None:
            return {"written": 0}
        version = await job_repository.refresh_canonical_content_version(uuid.UUID(canonical_job_id))
        results = await service.index_job(
            uuid.UUID(canonical_job_id), job, version.version if version else 1
        )
    return {"written": sum(result.written for result in results)}


@celery_app.task(name="embed.index_job")
def index_job_embeddings(canonical_job_id: str) -> dict[str, int]:
    return asyncio.run(_run_one(canonical_job_id))


async def _run_profile(user_id: str) -> dict[str, int]:
    async with session_scope() as session:
        service, _ = await _service(session)
        candidate_repository = CandidateRepository(session)
        profile = await candidate_repository.get_latest_candidate_profile(uuid.UUID(user_id))
        if profile is None:
            return {"written": 0}
        preferences = await candidate_repository.get_preferences(uuid.UUID(user_id))
        results = await service.index_profile(profile, preferences)
    return {"written": sum(result.written for result in results)}


@celery_app.task(name="embed.index_profile")
def index_profile_embeddings(user_id: str) -> dict[str, int]:
    """The candidate side of the same index. Runs whenever the profile changes —
    a new analysis or a skill correction both write a new revision, and a query
    built from a stale revision would retrieve for a CV the user has already
    edited."""
    return asyncio.run(_run_profile(user_id))


async def _run_backfill(offset: int) -> dict[str, int]:
    async with session_scope() as session:
        service, job_repository = await _service(session)
        canonical_job_ids = await job_repository.list_all_canonical_job_ids()
        batch = canonical_job_ids[offset : offset + _BACKFILL_BATCH]
        written = 0
        for canonical_job_id in batch:
            job = await job_repository.get_normalized_job_for_canonical(canonical_job_id)
            if job is None:
                continue
            version = await job_repository.refresh_canonical_content_version(canonical_job_id)
            results = await service.index_job(canonical_job_id, job, version.version if version else 1)
            written += sum(result.written for result in results)

        # Readiness is re-checked every pass, so a lane flips to "ready" as soon
        # as the backfill that was filling it catches up.
        await service.refresh_lane_readiness(len(canonical_job_ids))
        remaining = max(0, len(canonical_job_ids) - (offset + len(batch)))

    if remaining:
        backfill_embeddings.delay(offset + _BACKFILL_BATCH)
    return {"indexed": len(batch), "written": written, "remaining": remaining}


@celery_app.task(name="embed.backfill_embeddings")
def backfill_embeddings(offset: int = 0) -> dict[str, int]:
    """Walks the whole corpus in batches, re-queueing itself until every job is
    indexed in every configured lane. Safe to start at any time: already-indexed
    sections are recognised by their hash and skipped."""
    return asyncio.run(_run_backfill(offset))
