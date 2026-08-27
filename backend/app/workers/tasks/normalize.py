"""Maps a stored RawJob into a NormalizedJob and runs deduplication into a
CanonicalJob. See docs/domain-model.md (Raw -> Normalized -> Canonical)."""

from app.workers.celery_app import celery_app


@celery_app.task(name="normalize.process_raw_job")
def process_raw_job(raw_job_id: str) -> None:
    raise NotImplementedError
