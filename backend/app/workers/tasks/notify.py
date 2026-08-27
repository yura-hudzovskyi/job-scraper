"""Applies NotificationPolicy to a JobMatch and dispatches through
NotificationService. Must be safe to retry — see docs/notifications.md (Idempotency)."""

from app.workers.celery_app import celery_app


@celery_app.task(name="notify.dispatch_match")
def dispatch_match(job_match_id: str) -> None:
    raise NotImplementedError
