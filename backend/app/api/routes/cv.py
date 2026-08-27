"""CV upload/listing — see docs/api.md."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_current_user_id, get_cv_service
from app.domain.candidates.models import CvDocument
from app.services.cv_service import CvService, UnsupportedCvFormat

router = APIRouter(prefix="/api/cv", tags=["cv"])


class CvDocumentResponse(BaseModel):
    id: str
    filename: str
    uploaded_at: datetime
    text_preview: str


def _to_response(document: CvDocument) -> CvDocumentResponse:
    return CvDocumentResponse(
        id=document.id,
        filename=document.filename,
        uploaded_at=document.uploaded_at,
        text_preview=document.raw_text[:500],
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


@router.post("/analyze")
async def analyze_cv() -> None:
    """Extracting a structured CandidateProfile (skills, experience, roles) from a CV
    needs an LLM — Phase 2. See docs/roadmap.md."""
    raise NotImplementedError
