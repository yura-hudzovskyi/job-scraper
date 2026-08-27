"""Merges NormalizedJob records from different sources into one CanonicalJob.

Uses normalized company + normalized title + description hash + semantic similarity —
never a single exact-match heuristic, since the same vacancy is rarely byte-identical
across sources. See docs/domain-model.md.
"""

from app.domain.jobs.models import CanonicalJob, NormalizedJob


class DeduplicationService:
    def find_canonical_match(self, job: NormalizedJob) -> CanonicalJob | None:
        """Return an existing CanonicalJob this NormalizedJob likely belongs to, if any."""
        raise NotImplementedError

    def merge(self, canonical: CanonicalJob, job: NormalizedJob) -> CanonicalJob:
        """Attach a new source record to an existing canonical job."""
        raise NotImplementedError
