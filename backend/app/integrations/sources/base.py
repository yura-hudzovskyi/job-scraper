"""The one contract every job source must implement. See docs/source-adapters.md.

Nothing outside integrations/sources/<source>/ may depend on a specific source's
HTML/RSS shape — only on these types.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.domain.jobs.models import NormalizedJob, RawJob


@dataclass(frozen=True)
class JobSearchCriteria:
    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class JobFetchResult:
    raw_jobs: list[RawJob]
    next_cursor: str | None


@dataclass(frozen=True)
class SourceHealth:
    source_name: str
    consecutive_failures: int
    jobs_discovered: int
    parse_errors: int


class JobSourceAdapter(Protocol):
    source_name: str

    async def fetch_jobs(
        self,
        search: JobSearchCriteria,
        cursor: str | None = None,
    ) -> JobFetchResult:
        """Discover new/updated jobs matching the given criteria."""
        ...

    async def fetch_job_details(self, external_id: str, url: str) -> RawJob:
        """Fetch the full payload for a single job (e.g. detail page HTML)."""
        ...

    def normalize(self, raw_job: RawJob) -> NormalizedJob:
        """Map this source's raw payload into the source-independent NormalizedJob shape."""
        ...
