"""Creates/refreshes the embedding for a CanonicalJob via the configured
EmbeddingProvider. See docs/matching-engine.md, Stage 3."""

from app.workers.celery_app import celery_app


@celery_app.task(name="embed.embed_job")
def embed_job(canonical_job_id: str) -> None:
    raise NotImplementedError
