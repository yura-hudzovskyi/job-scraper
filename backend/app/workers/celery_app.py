from celery import Celery
from celery.signals import worker_process_init

from app.config.settings import get_settings
from app.db.session import reset_engine

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


@worker_process_init.connect
def _reset_db_engine_after_fork(**kwargs: object) -> None:
    """The prefork pool forks worker child processes after this module (and
    app.db.session) has already been imported once in the parent — without this,
    every forked child inherits and shares the parent's asyncpg connections, which
    corrupts them the moment more than one process touches the DB. See
    db/session.py::reset_engine.
    """
    reset_engine()
