"""Fetches new/updated jobs from one source via its JobSourceAdapter and stores them
as RawJob. Must be safe to run twice for the same source (see docs/source-adapters.md
for per-source health tracking / ScrapeRun)."""

from app.workers.celery_app import celery_app


@celery_app.task(name="scrape.fetch_source")
def fetch_source(source_name: str) -> None:
    raise NotImplementedError
