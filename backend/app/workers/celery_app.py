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

celery_app.conf.beat_schedule = {
    # search-profile-driven scrape intervals belong here once SearchProfile exists —
    # see docs/roadmap.md, Phase 1.
}
