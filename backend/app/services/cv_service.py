"""Use case: extract text from an uploaded CV and store it.

Turning this text into a full CandidateProfile (skills, experience, roles) needs an
LLM — that's Phase 2 (see docs/roadmap.md). Phase 1 stops at storing extracted text.
"""

import io
import uuid
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.domain.candidates.models import CvDocument
from app.repositories.candidate_repository import CandidateRepository


class UnsupportedCvFormat(ValueError):
    pass


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        return content.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        document = Document(io.BytesIO(content))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    raise UnsupportedCvFormat(f"unsupported CV format: {suffix or '(none)'}")


class CvService:
    def __init__(self, candidate_repository: CandidateRepository):
        self._candidate_repository = candidate_repository

    async def upload_cv(self, user_id: uuid.UUID, filename: str, content: bytes) -> CvDocument:
        raw_text = extract_text(filename, content)
        return await self._candidate_repository.save_cv_document(user_id, filename, raw_text)

    async def list_cvs(self, user_id: uuid.UUID) -> list[CvDocument]:
        return await self._candidate_repository.list_cv_documents(user_id)
