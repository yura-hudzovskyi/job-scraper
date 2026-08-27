"""List/get canonical jobs — see docs/api.md.

Match scores need the matching engine (Phase 2); save/apply/reject need the
application tracker (Phase 5) — see docs/roadmap.md.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_job_service
from app.domain.jobs.models import CanonicalJob
from app.services.job_service import JobService

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobSummaryResponse(BaseModel):
    id: str
    title: str
    company: str
    description: str
    source_count: int


def _to_summary(job: CanonicalJob) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=job.id,
        title=job.normalized.title,
        company=job.normalized.company,
        description=job.normalized.description,
        source_count=len(job.source_records),
    )


@router.get("", response_model=list[JobSummaryResponse])
async def list_jobs(job_service: JobService = Depends(get_job_service)) -> list[JobSummaryResponse]:
    jobs = await job_service.list_jobs()
    return [_to_summary(job) for job in jobs]


@router.get("/{job_id}", response_model=JobSummaryResponse)
async def get_job(
    job_id: uuid.UUID, job_service: JobService = Depends(get_job_service)
) -> JobSummaryResponse:
    job = await job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _to_summary(job)


@router.get("/{job_id}/match")
async def get_job_match(job_id: str) -> None:
    """Requires the matching engine — Phase 2. See docs/matching-engine.md."""
    raise NotImplementedError


@router.post("/{job_id}/rescore")
async def rescore_job(job_id: str) -> None:
    raise NotImplementedError


@router.post("/{job_id}/save")
async def save_job(job_id: str) -> None:
    """Requires the application tracker — Phase 5. See docs/roadmap.md."""
    raise NotImplementedError


@router.post("/{job_id}/apply")
async def apply_to_job(job_id: str) -> None:
    raise NotImplementedError


@router.post("/{job_id}/reject")
async def reject_job(job_id: str) -> None:
    raise NotImplementedError
