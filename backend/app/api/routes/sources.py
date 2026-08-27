"""Per-source health + manual sync trigger — see docs/source-adapters.md."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_job_repository
from app.integrations.sources.registry import build_default_registry
from app.repositories.job_repository import JobRepository
from app.workers.tasks.scrape import fetch_source

router = APIRouter(prefix="/api/sources", tags=["sources"])


class SourceHealthResponse(BaseModel):
    source_name: str
    raw_jobs_stored: int


@router.get("", response_model=list[SourceHealthResponse])
async def list_sources(
    job_repository: JobRepository = Depends(get_job_repository),
) -> list[SourceHealthResponse]:
    counts = await job_repository.count_raw_jobs_by_source()
    return [
        SourceHealthResponse(
            source_name=adapter.source_name, raw_jobs_stored=counts.get(adapter.source_name, 0)
        )
        for adapter in build_default_registry().all()
    ]


@router.post("/{source_id}/sync")
async def sync_source(source_id: str) -> dict[str, str]:
    fetch_source.delay(source_id)
    return {"status": "queued", "source": source_id}
