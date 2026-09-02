"""List/get vacancies and their match for the current user — see docs/api.md."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import (
    get_current_user_id,
    get_job_repository,
    get_job_service,
    get_match_repository,
)
from app.domain.jobs.models import CanonicalJob
from app.domain.matching.documents import job_document
from app.domain.matching.models import JobMatch
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.services.job_service import JobService
from app.workers.tasks.pipeline import match_user

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200


class JobMatchResponse(BaseModel):
    """The whole result, with the arithmetic visible. `score` is always
    reproducible from the other three: similarity when relevance is null,
    otherwise similarity*(1-weight) + relevance*weight."""

    id: str
    eligible: bool
    filter_reasons: list[str]
    score: float
    similarity: float
    relevance: float | None
    rerank_position: int | None
    recommendation: str
    embedding_model: str | None
    rerank_model: str | None
    rerank_weight: float | None
    decision: str
    scored_at: datetime | None


class JobSummaryResponse(BaseModel):
    id: str
    title: str
    company: str
    description: str
    source_count: int
    match: JobMatchResponse | None = None


class JobListResponse(BaseModel):
    items: list[JobSummaryResponse]
    total: int
    limit: int
    offset: int


class JobDetailResponse(JobSummaryResponse):
    # The exact text the embedding and rerank models were given for this
    # vacancy. Shown in the UI because "why did this score like that" is only
    # answerable if you can see what the model actually read.
    model_document: str


def _to_match_response(match: JobMatch | None) -> JobMatchResponse | None:
    if match is None:
        return None
    return JobMatchResponse(
        id=match.id,
        eligible=match.eligible,
        filter_reasons=match.filter_reasons,
        score=match.score,
        similarity=match.similarity,
        relevance=match.relevance,
        rerank_position=match.rerank_position,
        recommendation=match.recommendation.value,
        embedding_model=match.embedding_model,
        rerank_model=match.rerank_model,
        rerank_weight=match.rerank_weight,
        decision=match.decision.value,
        scored_at=match.scored_at,
    )


def _to_summary(job: CanonicalJob, match: JobMatch | None) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=job.id,
        title=job.normalized.title,
        company=job.normalized.company,
        description=job.normalized.description,
        source_count=len(job.source_records),
        match=_to_match_response(match),
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    limit: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    include_skipped: bool = Query(False),
    user_id: uuid.UUID = Depends(get_current_user_id),
    job_service: JobService = Depends(get_job_service),
) -> JobListResponse:
    jobs, matches, total = await job_service.list_jobs(
        user_id, limit, offset, include_skipped=include_skipped
    )
    return JobListResponse(
        items=[_to_summary(job, matches.get(job.id)) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    job_service: JobService = Depends(get_job_service),
    job_repository: JobRepository = Depends(get_job_repository),
    match_repository: MatchRepository = Depends(get_match_repository),
) -> JobDetailResponse:
    job = await job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    normalized = await job_repository.get_normalized_job_for_canonical(job_id)
    match = await match_repository.get_for_canonical_job(user_id, job_id)
    summary = _to_summary(job, match)
    return JobDetailResponse(
        **summary.model_dump(),
        model_document=job_document(normalized) if normalized else "",
    )


@router.post("/rematch")
async def rematch(user_id: uuid.UUID = Depends(get_current_user_id)) -> dict[str, str]:
    """Re-run embedding search and reranking for this user against the vacancies
    already in the database. What to press after editing preferences or uploading
    a new CV; the System page's "Run pipeline" is what also fetches new ones."""
    match_user.delay(str(user_id))
    return {"status": "queued"}
