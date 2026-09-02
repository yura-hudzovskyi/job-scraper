"""Celery app: one queue, three scheduled jobs, and the pipeline behind them.

There is deliberately no queue routing here. The old split existed to stop a
large LLM backfill starving interactive work; with the LLM layer gone the whole
pipeline is one sequential task per tick, and a second queue would only be a
second thing to configure identically in every deployment.
"""

from celery import Celery

from app.config.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "job_scraper",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.pipeline",
        "app.workers.tasks.notify",
        "app.workers.tasks.retention",
    ],
)

celery_app.conf.beat_schedule = {
    # Scrape, embed, match, notify — the whole pipeline, on a timer. This
    # interval is the one pipeline number that isn't editable from the System
    # page: beat reads its schedule at startup, so changing it needs a restart.
    "run-pipeline": {
        "task": "pipeline.run_full",
        "schedule": settings.scrape_interval_seconds,
    },
    "purge-stale-jobs": {
        "task": "retention.purge_stale_jobs",
        "schedule": 24 * 60 * 60,
    },
}
