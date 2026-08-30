"""Embedding-based role/title matching — same reasoning as skill_matching.py:
character-diff heuristics (the old `SequenceMatcher` approach) can't recognize
"Python Developer" ≈ "Backend Engineer" the way embeddings do, so this compares
job titles against a candidate's roles the same way skills are compared.
"""

from app.domain.matching.similarity import best_similarity
from app.integrations.ai.embeddings.base import EmbeddingProvider


class RoleMatcher:
    def __init__(self, embedding_provider: EmbeddingProvider):
        self._embedding_provider = embedding_provider

    async def assess(
        self, job_title: str, preferred_roles: list[str], profile_roles: list[str]
    ) -> float:
        """Preferred roles (explicitly configured) win when set; otherwise falls
        back to the roles the CV analysis actually derived (CandidateProfile.roles),
        so a candidate who never filled in a role preference still gets a real
        role-match signal instead of an unconditional 100 for every job title."""
        roles = preferred_roles or profile_roles
        if not roles:
            return 100.0
        vectors = await self._embedding_provider.embed([job_title, *roles])
        return best_similarity(vectors[0], vectors[1:]) * 100
