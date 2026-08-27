"""Stage 1 — cheap, deterministic eligibility filters. Run before any scoring.

Every check here must be answerable from data we actually have, and every rejection
must trace back to something the candidate explicitly configured in UserPreference —
no hardcoded universal rules (e.g. "reject anything mentioning security clearance")
that the candidate can't see or override. Two things docs/matching-engine.md mentions
as filters aren't implemented for that reason: an industries blacklist (NormalizedJob
has no industry field — nothing to check against yet) and salary-floor rejection when
the job's currency isn't USD (comparing $4000 against an unspecified-currency figure
would be a fabricated conversion, not a real check).
"""

import re
from dataclasses import dataclass, field

from app.domain.candidates.models import UserPreference
from app.domain.jobs.models import NormalizedJob


@dataclass(frozen=True)
class FilterResult:
    eligible: bool
    reasons: list[str] = field(default_factory=list)


def _mentions_keyword(text: str, keyword: str) -> bool:
    return re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE) is not None


class HardFilterService:
    def evaluate(self, job: NormalizedJob, preferences: UserPreference) -> FilterResult:
        """Reject jobs that fail non-negotiable constraints. Candidate ability
        (profile) plays no role here — that's what scoring is for; this stage only
        checks what the candidate has explicitly ruled out."""
        reasons = []

        if self._is_blacklisted_company(job, preferences):
            reasons.append(f'company "{job.company}" is blacklisted')

        blocked = self._mentioned_blocked_stack(job, preferences)
        if blocked:
            reasons.append(f"mentions blocked stack: {', '.join(blocked)}")

        if self._exceeds_max_experience(job, preferences):
            reasons.append(
                f"requires {job.required_experience_years}+ years, "
                f"candidate caps at {preferences.max_required_experience}"
            )

        if self._fails_salary_floor(job, preferences):
            assert job.salary is not None  # guaranteed by _fails_salary_floor
            reasons.append(
                f"salary tops out at {job.salary.max} {job.salary.currency}, "
                f"below desired {preferences.desired_salary_usd} USD"
            )

        if self._requires_only_remote_but_job_is_not(job, preferences):
            reasons.append("candidate wants remote-only; job isn't remote")

        if self._location_mismatch(job, preferences):
            reasons.append(
                f"job restricted to {job.location.countries + job.location.cities}, "
                f"outside candidate's {preferences.locations}"
            )

        return FilterResult(eligible=not reasons, reasons=reasons)

    def _is_blacklisted_company(self, job: NormalizedJob, preferences: UserPreference) -> bool:
        blacklisted = {name.strip().lower() for name in preferences.companies_blacklist}
        return job.company.strip().lower() in blacklisted

    def _mentioned_blocked_stack(self, job: NormalizedJob, preferences: UserPreference) -> list[str]:
        haystack = f"{job.title}\n{job.description}"
        return [
            keyword
            for keyword in preferences.blocked_stack
            if _mentions_keyword(haystack, keyword)
        ]

    def _exceeds_max_experience(self, job: NormalizedJob, preferences: UserPreference) -> bool:
        if job.required_experience_years is None or preferences.max_required_experience is None:
            return False
        return job.required_experience_years > preferences.max_required_experience

    def _fails_salary_floor(self, job: NormalizedJob, preferences: UserPreference) -> bool:
        if preferences.desired_salary_usd is None or job.salary is None:
            return False
        if job.salary.max is None or job.salary.currency != "USD":
            return False
        return job.salary.max < preferences.desired_salary_usd

    def _requires_only_remote_but_job_is_not(
        self, job: NormalizedJob, preferences: UserPreference
    ) -> bool:
        wants_remote_only = preferences.work_formats == ["remote"]
        return wants_remote_only and not job.location.remote

    def _location_mismatch(self, job: NormalizedJob, preferences: UserPreference) -> bool:
        job_places = [p.lower() for p in (*job.location.countries, *job.location.cities)]
        if not preferences.locations or not job_places:
            return False
        candidate_places = [loc.lower() for loc in preferences.locations]
        return not any(
            candidate in job_place or job_place in candidate
            for job_place in job_places
            for candidate in candidate_places
        )
