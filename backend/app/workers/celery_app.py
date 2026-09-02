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
        "app.workers.tasks.backfill",
        "app.workers.tasks.ai_ledger",
        "app.workers.tasks.enrich",
        "app.workers.tasks.retrieve",
    ],
)

# Separate queues so one kind of work can't starve another — see
# docs/ai-pipeline-v3.md (6.4). The split that matters: a "rescore everything"
# run is thousands of messages, and without its own queue it sits in front of the
# extraction and scoring of jobs scraped since, which is what the user is
# actually waiting for.
#
# Every worker must consume all of these (see docker-compose*.yml's -Q flag), or
# tasks routed to a queue nobody reads simply never run. Splitting them across
# dedicated workers is a deployment choice this routing makes possible, not a
# requirement.
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_routes = {
    # Fired right after a CV is analyzed or its skills corrected: the user is
    # watching for their jobs list to fill in.
    "backfill.score_existing_jobs_for_user": {"queue": "ai_interactive"},
    "backfill.*": {"queue": "ai_backfill"},
    "embed.backfill_embeddings": {"queue": "ai_backfill"},
    "embed.*": {"queue": "ai_extraction"},
    "extract.*": {"queue": "ai_extraction"},
    "score.*": {"queue": "ai_matching"},
    "retrieve.*": {"queue": "ai_matching"},
    # A user pressing "analyze" is waiting for the answer.
    "enrich.enrich_match": {"queue": "ai_interactive"},
    "enrich.*": {"queue": "ai_matching"},
}

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
    # Moves the AI call ledger from its Redis buffer into Postgres. Frequent
    # enough that the capped buffer never has to drop records under normal load.
    "flush-ai-invocations": {
        "task": "ai_ledger.flush",
        "schedule": 5 * 60,
    },
    # Once a day, spend what's left of the enrichment budget on the matches where
    # a second opinion could still change the user's decision. No-op unless
    # MATCHING_PIPELINE_V3 moved enrichment off the scoring path.
    "enrich-top-matches": {
        "task": "enrich.enrich_all_users",
        "schedule": 24 * 60 * 60,
    },
}
