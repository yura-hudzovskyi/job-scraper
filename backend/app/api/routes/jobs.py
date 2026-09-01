"""List/get canonical jobs and their match scores — see docs/api.md.

save/apply/reject need the application tracker — Phase 5, see docs/roadmap.md.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_current_user_id, get_job_service, get_match_repository
from app.domain.jobs.models import CanonicalJob
from app.domain.matching.models import JobMatch, LlmAssessment
from app.repositories.match_repository import MatchRepository
from app.services.job_service import JobService
from app.workers.tasks.backfill import rescore_all_jobs
from app.workers.tasks.score import score_job_for_user

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200


class JobSummaryResponse(BaseModel):
    id: str
    title: str
    company: str
    description: str
    source_count: int
    practical_fit: float | None = None
    recommendation: str | None = None


class JobListResponse(BaseModel):
    items: list[JobSummaryResponse]
    total: int
    limit: int
    offset: int


class ScoreBreakdownResponse(BaseModel):
    skills: float
    role: float
    experience: float
    semantic_fit: float
    salary: float
    location: float
    transferable_skills: float
    preferences: float


class LlmAssessmentResponse(BaseModel):
    overall_fit: float
    recommendation: str
    confidence: float
    strengths: list[str]
    gaps: list[str]
    critical_gaps: list[str]
    transferable_experience: list[str]
    interview_risk: str
    summary: str
    recommended_cv: str | None
    model_label: str


class MatchReasonResponse(BaseModel):
    label: str
    detail: str


class MatchGapResponse(BaseModel):
    label: str
    critical: bool


class JobMatchResponse(BaseModel):
    id: str
    eligible: bool
    requirement_match: float
    practical_fit: float
    breakdown: ScoreBreakdownResponse
    strengths: list[MatchReasonResponse]
    gaps: list[MatchGapResponse]
    recommendation: str | None
    llm_assessment: LlmAssessmentResponse | None
    skills_source: str | None
    scored_by: str | None
    scored_at: datetime | None


def _to_summary(job: CanonicalJob, match: JobMatch | None) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=job.id,
        title=job.normalized.title,
        company=job.normalized.company,
        description=job.normalized.description,
        source_count=len(job.source_records),
        practical_fit=match.practical_fit if match else None,
        recommendation=(
            match.recommendation.value if match and match.recommendation else None
        ),
    )


def _to_llm_assessment_response(assessment: LlmAssessment | None) -> LlmAssessmentResponse | None:
    if assessment is None:
        return None
    return LlmAssessmentResponse(
        overall_fit=assessment.overall_fit,
        recommendation=assessment.recommendation.value,
        confidence=assessment.confidence,
        strengths=assessment.strengths,
        gaps=assessment.gaps,
        critical_gaps=assessment.critical_gaps,
        transferable_experience=assessment.transferable_experience,
        interview_risk=assessment.interview_risk,
        summary=assessment.summary,
        recommended_cv=assessment.recommended_cv,
        model_label=assessment.model_label,
    )


def _to_match_response(match: JobMatch) -> JobMatchResponse:
    return JobMatchResponse(
        id=match.id,
        eligible=match.eligible,
        requirement_match=match.requirement_match,
        practical_fit=match.practical_fit,
        breakdown=ScoreBreakdownResponse(**vars(match.breakdown)),
        strengths=[
            MatchReasonResponse(label=reason.label, detail=reason.detail)
            for reason in match.strengths
        ],
        gaps=[MatchGapResponse(label=gap.label, critical=gap.critical) for gap in match.gaps],
        recommendation=match.recommendation.value if match.recommendation else None,
        llm_assessment=_to_llm_assessment_response(match.llm_assessment),
        skills_source=match.skills_source,
        scored_by=match.scored_by,
        scored_at=match.scored_at,
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
    items = [_to_summary(job, matches.get(job.id)) for job in jobs]
    return JobListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobSummaryResponse)
async def get_job(
    job_id: uuid.UUID, job_service: JobService = Depends(get_job_service)
) -> JobSummaryResponse:
    job = await job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _to_summary(job, match=None)


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


class RescoreAllRequest(BaseModel):
    # Overrides the Ollama fallback model for this run only — the "Rescore all
    # vacancies" admin action (Jobs page) re-extracts skills and rescores every
    # job through the Groq-first job-pipeline provider (see
    # workers/tasks/backfill.py, app/integrations/ai/llm/factory.py::
    # build_job_llm_provider), same as automatic per-scrape extraction and
    # AiMatcher; this only picks which local Ollama model that provider falls
    # back to once Groq's rate limit is hit mid-run, without touching server
    # config. None (the default) means "use whatever the server/System page is
    # already configured with." See app/api/routes/ai_settings.py for changing
    # Groq's own model persistently instead of per-run.
    llm_model: str | None = None


@router.post("/rescore-all")
async def rescore_all(
    payload: RescoreAllRequest, user_id: uuid.UUID = Depends(get_current_user_id)
) -> dict[str, str]:
    rescore_all_jobs.delay(str(user_id), payload.llm_model)
    return {"status": "queued"}


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
