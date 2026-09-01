"""CV upload/listing/analysis — see docs/api.md."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_current_user_id, get_cv_service
from app.domain.candidates.models import CandidateProfile, CvDocument
from app.services.ai_errors import LlmCallFailed, LlmNotConfigured
from app.services.cv_service import CvService, UnsupportedCvFormat
from app.workers.tasks.backfill import score_existing_jobs_for_user

router = APIRouter(prefix="/api/cv", tags=["cv"])


class CvDocumentResponse(BaseModel):
    id: str
    filename: str
    uploaded_at: datetime
    text_preview: str


class CandidateSkillResponse(BaseModel):
    name: str
    level: str
    years: float | None


class ExperienceEntryResponse(BaseModel):
    company: str
    title: str
    start_date: str
    end_date: str | None
    description: str
    skills: list[str]


class CandidateProfileResponse(BaseModel):
    id: str
    experience_years: float
    roles: list[str]
    skills: list[CandidateSkillResponse]
    experience: list[ExperienceEntryResponse]
    achievements: list[str]
    domains: list[str]
    ai_experience: list[str]
    generated_by: str | None


def _to_response(document: CvDocument) -> CvDocumentResponse:
    return CvDocumentResponse(
        id=document.id,
        filename=document.filename,
        uploaded_at=document.uploaded_at,
        text_preview=document.raw_text[:500],
    )


def _to_profile_response(profile: CandidateProfile) -> CandidateProfileResponse:
    return CandidateProfileResponse(
        id=profile.id,
        experience_years=profile.experience_years,
        roles=profile.roles,
        skills=[
            CandidateSkillResponse(name=skill.name, level=skill.level.value, years=skill.years)
            for skill in profile.skills
        ],
        experience=[
            ExperienceEntryResponse(
                company=entry.company,
                title=entry.title,
                start_date=entry.start_date,
                end_date=entry.end_date,
                description=entry.description,
                skills=entry.skills,
            )
            for entry in profile.experience
        ],
        achievements=profile.achievements,
        domains=profile.domains,
        ai_experience=profile.ai_experience,
        generated_by=profile.generated_by,
    )


@router.post("", response_model=CvDocumentResponse)
async def upload_cv(
    file: UploadFile,
    user_id: uuid.UUID = Depends(get_current_user_id),
    cv_service: CvService = Depends(get_cv_service),
) -> CvDocumentResponse:
    content = await file.read()
    try:
        document = await cv_service.upload_cv(user_id, file.filename or "cv.txt", content)
    except UnsupportedCvFormat as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(document)


@router.get("", response_model=list[CvDocumentResponse])
async def list_cvs(
    user_id: uuid.UUID = Depends(get_current_user_id),
    cv_service: CvService = Depends(get_cv_service),
) -> list[CvDocumentResponse]:
    documents = await cv_service.list_cvs(user_id)
    return [_to_response(document) for document in documents]


@router.delete("/{cv_id}", status_code=204)
async def delete_cv(
    cv_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    cv_service: CvService = Depends(get_cv_service),
) -> None:
    """Deleting a CV never deletes a CandidateProfile already extracted from it —
    see the ON DELETE SET NULL note on CandidateProfileModel.cv_document_id."""
    deleted = await cv_service.delete_cv(user_id, cv_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="CV not found")


@router.get("/profile", response_model=CandidateProfileResponse | None)
async def get_latest_profile(
    user_id: uuid.UUID = Depends(get_current_user_id),
    cv_service: CvService = Depends(get_cv_service),
) -> CandidateProfileResponse | None:
    """Returns the already-analyzed profile, if any — without re-running the LLM."""
    profile = await cv_service.get_latest_profile(user_id)
    return _to_profile_response(profile) if profile else None


@router.post("/analyze", response_model=CandidateProfileResponse)
async def analyze_cv(
    user_id: uuid.UUID = Depends(get_current_user_id),
    cv_service: CvService = Depends(get_cv_service),
) -> CandidateProfileResponse:
    """Analyzes the most recently uploaded CV into a structured CandidateProfile."""
    documents = await cv_service.list_cvs(user_id)
    if not documents:
        raise HTTPException(status_code=404, detail="no CV uploaded yet")
    latest = documents[0]

    try:
        profile = await cv_service.analyze_cv(user_id, uuid.UUID(latest.id), latest.raw_text)
    except LlmNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmCallFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Score this (newly analyzed or re-analyzed) profile against every existing
    # job, not just ones scraped from now on — see workers/tasks/backfill.py.
    score_existing_jobs_for_user.delay(str(user_id))

    return _to_profile_response(profile)
