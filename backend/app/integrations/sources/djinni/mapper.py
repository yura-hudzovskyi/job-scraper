"""Maps Djinni's parsed payload into a source-independent NormalizedJob."""

from app.domain.jobs.models import NormalizedJob, RawJob


def to_normalized_job(raw_job: RawJob) -> NormalizedJob:
    raise NotImplementedError
