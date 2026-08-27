"""Stage 1 — cheap, deterministic eligibility filters. Run before any scoring.

See docs/matching-engine.md.
"""

from dataclasses import dataclass

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.jobs.models import NormalizedJob


@dataclass(frozen=True)
class FilterResult:
    eligible: bool
    reasons: list[str]


class HardFilterService:
    def evaluate(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
    ) -> FilterResult:
        """Reject jobs that fail non-negotiable constraints (relocation, salary floor,
        clearance, blocked stack, experience far beyond the candidate's, etc.)."""
        raise NotImplementedError
