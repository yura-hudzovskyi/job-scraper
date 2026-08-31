"""Applies NotificationPolicy to a scored JobMatch and dispatches through
NotificationService. Must be safe to retry — delivery is upserted per
(notification_id, channel), see docs/notifications.md.
"""

import asyncio
import uuid

from app.config.settings import get_settings
from app.db.session import session_scope
from app.domain.notifications.models import JobMatchNotification
from app.domain.notifications.policy import NotificationPolicy
from app.integrations.notifications.factory import build_telegram_provider
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository
from app.services.notification_service import NotificationService
from app.workers.celery_app import celery_app


async def _run(user_id: str, canonical_job_id: str) -> dict[str, bool | str]:
    async with session_scope() as session:
        match_repository = MatchRepository(session)
        job_repository = JobRepository(session)
        notification_repository = NotificationRepository(session)

        match = await match_repository.get_for_canonical_job(
            uuid.UUID(user_id), uuid.UUID(canonical_job_id)
        )
        if match is None:
            raise LookupError(f"job {canonical_job_id} has no JobMatch for user {user_id} yet")

        job = await job_repository.get_normalized_job_for_canonical(uuid.UUID(canonical_job_id))
        if job is None:
            raise LookupError(f"canonical job {canonical_job_id} has no normalized source record")

        provider = await build_telegram_provider(
            uuid.UUID(user_id), notification_repository, get_settings()
        )
        if provider is None:
            return {"sent": False, "reason": "no Telegram bot connected"}

        source_links = await job_repository.list_source_links_for_canonical(
            uuid.UUID(canonical_job_id)
        )

        policy_config = await notification_repository.get_notification_policy_config(
            uuid.UUID(user_id)
        )
        service = NotificationService(
            NotificationPolicy(policy_config), provider, notification_repository
        )
        notification = JobMatchNotification(
            match=match,
            job_title=job.title,
            company=job.company,
            source_links=source_links or [(job.source, job.url)],
            salary=job.salary,
            seniority=job.seniority,
            required_experience_years=job.required_experience_years,
            remote=job.location.remote,
        )
        sent = await service.notify_if_relevant(uuid.UUID(user_id), notification)

    return {"sent": sent}


@celery_app.task(name="notify.dispatch_match")
def dispatch_match(user_id: str, canonical_job_id: str) -> dict[str, bool | str]:
    return asyncio.run(_run(user_id, canonical_job_id))
