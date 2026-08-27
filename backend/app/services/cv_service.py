"""Use case: upload a CV, extract a CandidateProfile from it via the LLM provider,
and persist it. Orchestrates integrations.ai + repositories — no HTTP or business
rules of its own."""

from app.domain.candidates.models import CandidateProfile
from app.integrations.ai.llm.base import LLMProvider
from app.repositories.candidate_repository import CandidateRepository


class CvService:
    def __init__(self, llm_provider: LLMProvider, candidate_repository: CandidateRepository):
        self._llm_provider = llm_provider
        self._candidate_repository = candidate_repository

    async def analyze_cv(self, user_id: str, cv_text: str) -> CandidateProfile:
        raise NotImplementedError
