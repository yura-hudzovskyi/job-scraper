"""Use case: user preferences (fully structured, no AI needed) and a basic profile
summary. Full CandidateProfile extraction from a CV is Phase 2 — see docs/roadmap.md.

suggest_preferences is the one AI-assisted exception: it reads the already-extracted
CandidateProfile and proposes a starting set of preferences via LLM, so a user
doesn't have to fill out every field from scratch. It never saves anything — see its
own docstring — preferences stay "fully structured, no AI needed" in the sense that
matters (edited/saved values are always literally what the user set, an AI
suggestion is just a form-filling convenience the user can freely edit or discard).
"""

import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.domain.candidates.models import CvDocument, UserPreference
from app.integrations.ai.llm.base import LLMProvider
from app.repositories.candidate_repository import CandidateRepository


@dataclass(frozen=True)
class ProfileSummary:
    user_id: str
    cv_documents: list[CvDocument]
    has_preferences: bool


class LlmNotConfigured(RuntimeError):
    pass


class _SuggestedPreferences(BaseModel):
    desired_salary_usd: int | None = None
    preferred_roles: list[str] = Field(default_factory=list)
    preferred_stack: list[str] = Field(default_factory=list)
    acceptable_stack: list[str] = Field(default_factory=list)
    work_formats: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    max_required_experience: float | None = None


_SUGGEST_PROMPT = """Suggest starting job-search preferences for this candidate, based \
only on their CV-derived profile below. Be conservative: leave a field at its neutral \
default (null, or an empty list) rather than guessing when the profile gives no real \
signal — a wrong guess here silently filters out jobs the candidate would actually want.

- desired_salary_usd: a reasonable market-rate ballpark for this role/seniority/experience \
level, in USD. Only give a number if you have enough signal to make an educated guess.
- preferred_roles: job titles matching this candidate's actual experience (their `roles` \
below is a good starting point, expand slightly to close synonyms only if confident).
- preferred_stack: the technologies this candidate is strongest in (expert/strong skills).
- acceptable_stack: technologies they know but less deeply (commercial/aware skills) —
  willing to work with, not their top choice.
- work_formats: only include "remote" if there's a clear signal (e.g. remote experience
  listed) — otherwise leave empty rather than assuming.
- locations: only include a location if the CV explicitly mentions it (current location,
  stated relocation willingness) — otherwise leave empty.
- max_required_experience: roughly this candidate's own experience_years plus a small
  margin (a posting asking for meaningfully more than they have is a stretch, not a fit).

Candidate profile:
- Total experience: {experience_years} years
- Roles: {roles}
- Skills (name: level): {skills}
- Domains: {domains}
- Achievements: {achievements}
"""


@dataclass(frozen=True)
class SuggestedPreferences:
    """A proposed UserPreference plus which model produced it — never persisted by
    itself, see ProfileService.suggest_preferences."""

    preferences: UserPreference
    model_label: str


class ProfileService:
    def __init__(
        self,
        candidate_repository: CandidateRepository,
        llm_provider: LLMProvider | None = None,
    ):
        self._candidate_repository = candidate_repository
        self._llm_provider = llm_provider

    async def get_profile_summary(self, user_id: uuid.UUID) -> ProfileSummary:
        cv_documents = await self._candidate_repository.list_cv_documents(user_id)
        preferences = await self._candidate_repository.get_preferences(user_id)
        return ProfileSummary(
            user_id=str(user_id),
            cv_documents=cv_documents,
            has_preferences=preferences is not None,
        )

    async def get_preferences(self, user_id: uuid.UUID) -> UserPreference | None:
        return await self._candidate_repository.get_preferences(user_id)

    async def update_preferences(
        self, user_id: uuid.UUID, preferences: UserPreference
    ) -> UserPreference:
        return await self._candidate_repository.save_preferences(user_id, preferences)

    async def suggest_preferences(self, user_id: uuid.UUID) -> SuggestedPreferences:
        """Returns a suggestion for the caller to show/let the user review — does
        NOT save it. Preferences directly gate hard filters (see
        domain/matching/filters.py); auto-saving an LLM guess could silently reject
        jobs the candidate would actually want, so this is a fill-the-form
        convenience, never a background write."""
        if self._llm_provider is None:
            raise LlmNotConfigured(
                "no LLM provider configured — set GEMINI_API_KEY or LLM_PROVIDER"
            )

        profile = await self._candidate_repository.get_latest_candidate_profile(user_id)
        if profile is None:
            raise LookupError(f"user {user_id} has no analyzed CandidateProfile yet")

        prompt = _SUGGEST_PROMPT.format(
            experience_years=profile.experience_years,
            roles=", ".join(profile.roles) or "none listed",
            skills=", ".join(f"{skill.name}: {skill.level.value}" for skill in profile.skills)
            or "none listed",
            domains=", ".join(profile.domains) or "none listed",
            achievements=", ".join(profile.achievements) or "none listed",
        )
        result = await self._llm_provider.structured_completion(prompt, _SuggestedPreferences)
        suggested = result.data

        return SuggestedPreferences(
            preferences=UserPreference(
                user_id=str(user_id),
                desired_salary_usd=suggested.desired_salary_usd,
                preferred_roles=suggested.preferred_roles,
                preferred_stack=suggested.preferred_stack,
                acceptable_stack=suggested.acceptable_stack,
                work_formats=suggested.work_formats,
                locations=suggested.locations,
                max_required_experience=suggested.max_required_experience,
            ),
            model_label=result.model_label,
        )
