from celery import Celery

from app.config.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "job_scraper",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.scrape",
        "app.workers.tasks.normalize",
        "app.workers.tasks.embed",
        "app.workers.tasks.extract_job_skills",
        "app.workers.tasks.score",
        "app.workers.tasks.notify",
        "app.workers.tasks.retention",
    ],
)

# Each tick scrapes one category (rotating through app/integrations/sources/
# categories.py over time, see workers/tasks/scrape.py) rather than everything at
# once — a shorter interval than the old fixed 2h is what makes the rotation reach
# every category in a reasonable time instead of taking weeks.
celery_app.conf.beat_schedule = {
    "scrape-dou": {
        "task": "scrape.fetch_source",
        "schedule": settings.scrape_interval_seconds,
        "args": ("dou",),
    },
    "scrape-djinni": {
        "task": "scrape.fetch_source",
        "schedule": settings.scrape_interval_seconds,
        "args": ("djinni",),
    },
    "purge-stale-jobs": {
        "task": "retention.purge_stale_jobs",
        "schedule": 24 * 60 * 60,
    },
}
