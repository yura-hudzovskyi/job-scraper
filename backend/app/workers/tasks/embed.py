"""Would create/refresh a cached embedding for a CanonicalJob.

Not implemented: SemanticScorer currently computes both the candidate-profile and
job embeddings on demand inside score.score_job_for_user, which is simple and
perfectly fine at personal-project scale. A persisted pgvector-backed cache (and the
"find similar jobs" queries it would unlock) is a scale-driven optimization for later,
not a Phase 2 requirement — see docs/roadmap.md.
"""

from app.workers.celery_app import celery_app


@celery_app.task(name="embed.embed_job")
def embed_job(canonical_job_id: str) -> None:
    raise NotImplementedError
