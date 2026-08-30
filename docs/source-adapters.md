# Source adapters

No module outside `backend/app/integrations/sources/<source>/` is allowed to know how a
specific source works — not "how DOU's RSS feed is shaped," not "what DOU's job detail
HTML looks like." Everything else in the platform talks to `NormalizedJob`.

## Contract

```python
class JobSourceAdapter(Protocol):
    source_name: str

    async def fetch_jobs(
        self,
        search: JobSearchCriteria,
        cursor: str | None = None,
    ) -> JobFetchResult: ...

    async def fetch_job_details(
        self,
        external_id: str,
        url: str,
    ) -> RawJob: ...

    def normalize(self, raw_job: RawJob) -> NormalizedJob: ...
```

See `backend/app/integrations/sources/base.py` for the actual types.

## Adding a new source

1. Implement `JobSourceAdapter` in a new `backend/app/integrations/sources/<name>/`
   package (`adapter.py`, `parser.py`, `mapper.py`).
2. Register it in `backend/app/integrations/sources/registry.py`.
3. Done. Matching, dedup, notifications and the UI don't change.

## DOU

DOU's job board exposes an RSS feed, which is a far more stable discovery mechanism
than scraping the listing HTML. The adapter should:

```text
DOU RSS → new vacancy discovered → fetch detail page → parse → normalize
```

i.e. use RSS for discovery, and only fetch full HTML for the detail/description of a
vacancy that's actually new.

## Djinni

Djinni's vacancy and search-result pages are plain HTML fetched with `httpx` and parsed
with `BeautifulSoup`/`lxml`. Playwright is a fallback only, used exclusively when data is
genuinely unavailable without JS execution.

**The platform does not build around defeating anti-bot measures.** If a source blocks
or disallows scraping, the adapter must fail gracefully (mark itself degraded, stop) —
not escalate into CAPTCHA solving, proxy rotation, or anything resembling a botnet.

## Fault isolation

Each adapter tracks its own health independently:

```json
{
  "source": "djinni",
  "last_success_at": "...",
  "last_failure_at": "...",
  "consecutive_failures": 0,
  "jobs_discovered": 0,
  "parse_errors": 0
}
```

If a source changes its markup and a parser starts failing, that source's health goes
`DEGRADED` — the rest of the platform, including other sources, keeps running.

Each scrape execution is also recorded as a `ScrapeRun`:

```json
{
  "source": "djinni",
  "category": "Python",
  "started_at": "...",
  "finished_at": "...",
  "jobs_seen": 82,
  "new_count": 13,
  "errors": 0
}
```

## Category rotation

Each scrape tick covers **one category**, not a source's entire default feed. Every
category configured per source in `app/integrations/sources/categories.py` gets a
turn: `JobRepository.get_least_recently_scraped_category` reads `scrape_runs` and
picks whichever category has gone longest without a run (a category with no run at
all always wins), via the pure `app/domain/jobs/scrape_rotation.py::pick_next_category`.

This exists because the platform used to always scrape with empty keywords —
DOU's and Djinni's *generic* default feed — even though both sites support
filtering by category (`?category=Artist` on DOU, `?primary_keyword=Design` on
Djinni). A candidate outside mainstream software roles (e.g. a 3D artist) would
never see a relevant job, not because matching was bad, but because the scraper
never asked the source for that category at all.

Each run is capped at `Settings.scrape_max_jobs_per_run` listings
(`JobIngestionService.ingest_source`'s `max_jobs` parameter) — a safety ceiling on
per-run cost, not a precise "N new jobs" guarantee (already-known listings are
still skipped for free within that cap). `Settings.scrape_interval_seconds`
controls how often a tick fires per source.

## Retention

Higher category coverage means more jobs accumulate over time, so
`retention.purge_stale_jobs` runs once daily and deletes any `canonical_jobs` row
(and everything that references it — `job_matches`, `notifications`, `applications`,
`job_source_records`, and now-orphaned `raw_jobs`) whose `last_seen_at` is older
than `Settings.job_retention_days` (18 by default). See
`app/services/job_retention_service.py` for the exact cross-table delete
ordering — no foreign key in the schema sets `ondelete=`, so a child row must be
deleted before its parent, and the service does that explicitly rather than
relying on DB-level cascade behavior.

## Parser fixture tests

Every adapter's parser is tested against saved HTML/RSS fixtures, not live requests:

```text
backend/tests/fixtures/
    dou/
        listing.html
        vacancy.html
    djinni/
        listing.html
        vacancy.html
```

When a source changes its markup, the fixture test fails in CI immediately — instead of
silently returning empty results in production.
