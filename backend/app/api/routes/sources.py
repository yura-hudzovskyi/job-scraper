"""Per-source health — see docs/source-adapters.md.

There is no per-source sync trigger any more: scraping one source without
embedding and matching what it found leaves the app in a half-updated state
nobody asked for. The System page's "Run pipeline" does the whole thing.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user_id, get_job_repository
from app.integrations.sources.categories import CATEGORIES_BY_SOURCE
from app.integrations.sources.registry import build_default_registry
from app.repositories.job_repository import JobRepository

router = APIRouter(prefix="/api/sources", tags=["sources"])


class ScrapeRunResponse(BaseModel):
    source: str
    category: str | None
    started_at: str
    jobs_seen: int
    new_count: int
    errors: int


class SourceHealthResponse(BaseModel):
    source_name: str
    raw_jobs_stored: int
    # Every category this source rotates through — one per run, longest-unscraped
    # first. Shown so "why haven't I seen any QA jobs" has a visible answer.
    categories: list[str]


@router.get("", response_model=list[SourceHealthResponse])
async def list_sources(
    user_id: uuid.UUID = Depends(get_current_user_id),
    job_repository: JobRepository = Depends(get_job_repository),
) -> list[SourceHealthResponse]:
    counts = await job_repository.count_raw_jobs_by_source()
    return [
        SourceHealthResponse(
            source_name=adapter.source_name,
            raw_jobs_stored=counts.get(adapter.source_name, 0),
            categories=list(CATEGORIES_BY_SOURCE.get(adapter.source_name, [])),
        )
        for adapter in build_default_registry().all()
    ]


@router.get("/runs", response_model=list[ScrapeRunResponse])
async def list_scrape_runs(
    limit: int = 20,
    user_id: uuid.UUID = Depends(get_current_user_id),
    job_repository: JobRepository = Depends(get_job_repository),
) -> list[ScrapeRunResponse]:
    runs = await job_repository.list_recent_scrape_runs(max(1, min(limit, 100)))
    return [
        ScrapeRunResponse(
            source=run.source,
            category=run.category,
            started_at=run.started_at.isoformat(),
            jobs_seen=run.jobs_seen,
            new_count=run.new_count,
            errors=run.errors,
        )
        for run in runs
    ]
