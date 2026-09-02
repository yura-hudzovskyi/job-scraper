"""CV upload, listing and deletion — see docs/api.md.

The most recently uploaded CV is the active one: it is what gets embedded and
what the reranker reads. There is no analysis endpoint because there is no
analysis step — the text goes to the models as-is.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_candidate_repository, get_current_user_id, get_cv_service
from app.domain.candidates.models import CvDocument
from app.domain.matching.documents import profile_document
from app.repositories.candidate_repository import CandidateRepository
from app.services.cv_service import CvService, EmptyCv, UnsupportedCvFormat
from app.workers.tasks.pipeline import match_user

router = APIRouter(prefix="/api/cv", tags=["cv"])


class CvDocumentResponse(BaseModel):
    id: str
    filename: str
    uploaded_at: datetime
    characters: int
    text_preview: str
    # True for the CV that is actually used. Exactly one, always the newest.
    active: bool


class ActiveCvResponse(BaseModel):
    """The active CV plus the exact document built from it. That text is what the
    embedding and rerank models see, so showing it is the difference between
    "trust the score" and "check the score"."""

    cv: CvDocumentResponse | None
    model_document: str


def _to_response(document: CvDocument, active: bool) -> CvDocumentResponse:
    return CvDocumentResponse(
        id=document.id,
        filename=document.filename,
        uploaded_at=document.uploaded_at,
        characters=len(document.raw_text),
        text_preview=document.raw_text[:500],
        active=active,
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
    except (UnsupportedCvFormat, EmptyCv) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # This is now the active CV, so every existing match was scored against a
    # different candidate. Re-matching immediately is what makes that visible.
    match_user.delay(str(user_id))
    return _to_response(document, active=True)


@router.get("", response_model=list[CvDocumentResponse])
async def list_cvs(
    user_id: uuid.UUID = Depends(get_current_user_id),
    cv_service: CvService = Depends(get_cv_service),
) -> list[CvDocumentResponse]:
    documents = await cv_service.list_cvs(user_id)
    return [_to_response(document, active=index == 0) for index, document in enumerate(documents)]


@router.get("/active", response_model=ActiveCvResponse)
async def get_active_cv(
    user_id: uuid.UUID = Depends(get_current_user_id),
    cv_service: CvService = Depends(get_cv_service),
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
) -> ActiveCvResponse:
    document = await cv_service.get_active_cv(user_id)
    if document is None:
        return ActiveCvResponse(cv=None, model_document="")
    preferences = await candidate_repository.get_preferences(user_id)
    return ActiveCvResponse(
        cv=_to_response(document, active=True),
        model_document=profile_document(document.raw_text, preferences),
    )


@router.delete("/{cv_id}", status_code=204)
async def delete_cv(
    cv_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    cv_service: CvService = Depends(get_cv_service),
) -> None:
    """Deleting the active CV promotes the next-newest one, so matching keeps
    working with whatever is left."""
    if not await cv_service.delete_cv(user_id, cv_id):
        raise HTTPException(status_code=404, detail="CV not found")
    match_user.delay(str(user_id))
