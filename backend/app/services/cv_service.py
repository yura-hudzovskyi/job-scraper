"""Use case: get the text out of an uploaded CV and store it.

That is the whole of it. There is no analysis step and no extracted profile: the
CV's text *is* the candidate side of the pipeline, embedded and reranked
verbatim. Nothing sits between what the user uploaded and what the models read,
which is why the Profile page can show them the exact document.
"""

import io
import uuid
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.domain.candidates.models import CvDocument
from app.domain.documents.models import EntityKind
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.document_repository import DocumentRepository


class UnsupportedCvFormat(ValueError):
    pass


class EmptyCv(ValueError):
    """A file that parsed fine but yielded no text — typically a scanned PDF with
    no text layer. Worth failing loudly at upload: an empty CV would otherwise
    embed happily and quietly match nothing."""


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
    def __init__(
        self,
        candidate_repository: CandidateRepository,
        document_repository: DocumentRepository | None = None,
    ):
        self._candidate_repository = candidate_repository
        self._document_repository = document_repository

    async def upload_cv(self, user_id: uuid.UUID, filename: str, content: bytes) -> CvDocument:
        raw_text = extract_text(filename, content)
        if not raw_text.strip():
            raise EmptyCv(
                "no text could be read from this file — if it's a scanned PDF, "
                "upload a text-based one instead"
            )
        return await self._candidate_repository.save_cv_document(user_id, filename, raw_text)

    async def list_cvs(self, user_id: uuid.UUID) -> list[CvDocument]:
        return await self._candidate_repository.list_cv_documents(user_id)

    async def get_active_cv(self, user_id: uuid.UUID) -> CvDocument | None:
        return await self._candidate_repository.get_active_cv(user_id)

    async def delete_cv(self, user_id: uuid.UUID, cv_document_id: uuid.UUID) -> bool:
        """The CV's revisions go first: they hold a foreign key to it, and nothing
        in this schema cascades. Ownership is checked by the delete below, so the
        revisions are only cleared once that delete confirms the CV was this
        user's — order matters here in both directions."""
        if not await self._candidate_repository.owns_cv_document(user_id, cv_document_id):
            return False
        if self._document_repository is not None:
            await self._document_repository.delete_for_owners(
                EntityKind.CANDIDATE, [cv_document_id]
            )
        return await self._candidate_repository.delete_cv_document(user_id, cv_document_id)
