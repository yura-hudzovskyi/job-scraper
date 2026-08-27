"""Runs MatchingService for a (user, canonical_job) pair and upserts the JobMatch.
LLM reranking only runs for shortlisted matches — see docs/matching-engine.md."""

from app.workers.celery_app import celery_app


@celery_app.task(name="score.score_job_for_user")
def score_job_for_user(user_id: str, canonical_job_id: str) -> None:
    raise NotImplementedError
