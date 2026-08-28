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
        "app.workers.tasks.score",
        "app.workers.tasks.notify",
    ],
)

_SCRAPE_INTERVAL_SECONDS = 2 * 60 * 60  # 2h, per docs/source-adapters.md's example cadence

# Per-source-only for now — no keyword filtering, since SearchProfile (multiple named
# searches per source, each with its own keywords/interval) doesn't exist yet. See
# docs/roadmap.md; this is a fixed stand-in for that.
celery_app.conf.beat_schedule = {
    "scrape-dou": {
        "task": "scrape.fetch_source",
        "schedule": _SCRAPE_INTERVAL_SECONDS,
        "args": ("dou", []),
    },
    "scrape-djinni": {
        "task": "scrape.fetch_source",
        "schedule": _SCRAPE_INTERVAL_SECONDS,
        "args": ("djinni", []),
    },
}
