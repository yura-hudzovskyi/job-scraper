"""List/get canonical jobs and their match scores — see docs/api.md.

save/apply/reject need the application tracker — Phase 5, see docs/roadmap.md.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user_id, get_job_service, get_match_repository
from app.domain.jobs.models import CanonicalJob
from app.domain.matching.models import JobMatch
from app.repositories.match_repository import MatchRepository
from app.services.job_service import JobService
from app.workers.tasks.score import score_job_for_user

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobSummaryResponse(BaseModel):
    id: str
    title: str
    company: str
    description: str
    source_count: int


class ScoreBreakdownResponse(BaseModel):
    skills: float
    role: float
    experience: float
    semantic_fit: float
    salary: float
    location: float
    transferable_skills: float
    preferences: float


class JobMatchResponse(BaseModel):
    id: str
    eligible: bool
    requirement_match: float
    practical_fit: float
    breakdown: ScoreBreakdownResponse
    strengths: list[str]
    gaps: list[str]
    recommendation: str | None
    skills_source: str | None


def _to_summary(job: CanonicalJob) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=job.id,
        title=job.normalized.title,
        company=job.normalized.company,
        description=job.normalized.description,
        source_count=len(job.source_records),
    )


def _to_match_response(match: JobMatch) -> JobMatchResponse:
    return JobMatchResponse(
        id=match.id,
        eligible=match.eligible,
        requirement_match=match.requirement_match,
        practical_fit=match.practical_fit,
        breakdown=ScoreBreakdownResponse(**vars(match.breakdown)),
        strengths=[reason.label for reason in match.strengths],
        gaps=[gap.label for gap in match.gaps],
        recommendation=match.recommendation.value if match.recommendation else None,
        skills_source=match.skills_source,
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


@router.get("/{job_id}/match", response_model=JobMatchResponse)
async def get_job_match(
    job_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    match_repository: MatchRepository = Depends(get_match_repository),
) -> JobMatchResponse:
    match = await match_repository.get_for_canonical_job(user_id, job_id)
    if match is None:
        raise HTTPException(
            status_code=404, detail="not scored yet — POST /rescore first"
        )
    return _to_match_response(match)


@router.post("/{job_id}/rescore")
async def rescore_job(
    job_id: uuid.UUID, user_id: uuid.UUID = Depends(get_current_user_id)
) -> dict[str, str]:
    score_job_for_user.delay(str(user_id), str(job_id))
    return {"status": "queued", "job_id": str(job_id)}


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
