# Architecture

## Mission

Aggregate vacancies from multiple job boards, normalize them into a
source-independent model, rank them against the candidate's CV using embedding
search and reranking, and deliver the good ones through configurable
notification channels — with every number it produces traceable to the two
inputs it came from.

## Principles

These are load-bearing constraints, not suggestions. Check a module against them
before merging it.

1. **Adapters, not special cases.** Job sources, model providers and notification
   channels sit behind narrow interfaces. Domain logic must never import DOU,
   Djinni, Telegram or FastAPI directly.

2. **Two model calls, and nothing else.** The pipeline's entire use of AI is
   Voyage's embedding endpoint and Voyage's rerank endpoint. There is no LLM in
   this codebase — no skill extraction, no role classification, no generated
   verdicts, no summaries. Anything that reads like judgement is either one of
   those two numbers or a deterministic rule the user configured.

   This is a deliberate trade, and it's worth naming what it costs: the system is
   good at *"is this vacancy in the neighbourhood of my experience"* and has no
   opinion whatsoever on *"do I meet requirement 3"*. Claiming the latter needs
   an LLM; pretending to it without one is worse than not answering.

3. **A score is an arithmetic result, not a verdict.** Every match stores its
   similarity, its rerank relevance and the weight between them, and the UI shows
   the sum. A number a user can't reconstruct is a bug. A vacancy the reranker
   never read says `not reranked` rather than showing a zero, and a vacancy a
   filter removed stores the rule it broke rather than vanishing.

4. **Deterministic rules stay deterministic.** Hard filters — blocked stack,
   salary floor, locations, blacklists — are the candidate's own non-negotiable
   constraints. They run before scoring, they are never handed to a model to
   reinterpret, and every rejection names the rule.

5. **Experience ≠ preference.** What the candidate has done (the CV's text) is
   separate from what they want (`UserPreference`), and within preferences, what
   goes into the *query* is separate from what acts as a *filter*. See
   [docs/domain-model.md](docs/domain-model.md).

6. **Raw ≠ normalized ≠ canonical.** Scraped payloads are stored as-is
   (`RawJob`), mapped into a source-independent shape (`JobSourceRecord`), then
   deduplicated into one `CanonicalJob` per real vacancy. Changing a parser must
   never require touching matching or notifications.

7. **Configuration belongs in the UI.** Every number the pipeline runs on —
   models, batch sizes, the rerank weight, thresholds, retention — lives in the
   database and is edited from the System page, with its own explanation rendered
   next to it. Only deployment secrets stay in `.env`.

8. **Idempotent background work.** Scraping, embedding, matching and delivery are
   all safe to retry: `unique(source, external_id)`,
   `unique(user_id, canonical_job_id)`, `unique(document_type, document_id,
   model)`, `unique(notification_id, channel)`. Re-running the pipeline over an
   unchanged corpus costs no API calls at all.

9. **One source's failure is not the platform's failure.** A broken Djinni parser
   is recorded on that scrape run; DOU keeps working; the run continues.

10. **Modular monolith.** One API + one worker pool, organized into clean domain
    modules. Distribute only if a concrete scaling problem demands it.

## System overview

```text
┌─────────────────────────────────────────────┐
│                React Web App                 │
└──────────────────────┬───────────────────────┘
                       │ REST
┌──────────────────────▼───────────────────────┐
│                  FastAPI API                  │
│  auth │ cv │ jobs │ settings │ sources        │
│  telegram │ system (config, run, reset)       │
└──────────────┬────────────────────┬───────────┘
               │                    │
        PostgreSQL              Redis
        + pgvector             (queue only)
               │                    │
               │           ┌────────▼────────────┐
               │           │  Celery worker       │
               │           │  pipeline.run_full   │
               │           │  notify.dispatch     │
               │           │  retention.purge     │
               │           └───────┬──────────────┘
               │                   │
         ┌─────▼───────────────────▼──────┐
         │       External providers        │
         │  DOU │ Djinni │ Voyage │ Telegram │
         └────────────────────────────────┘
```

### Pipeline

```text
scrape  ->  embed  ->  match  ->  notify
```

One Celery task, run on a timer and by the System page's button — the same task
either way. Each step records its counts on a `pipeline_runs` row as it finishes,
so a run that produced nothing still explains why. See
[docs/pipeline.md](docs/pipeline.md).

## Tech stack

| Layer          | Choice                                                          |
|----------------|-----------------------------------------------------------------|
| Frontend       | React, TypeScript, Vite, TanStack Query, React Router, Tailwind |
| Backend        | Python 3.13, FastAPI, Pydantic, SQLAlchemy 2, Alembic           |
| Database       | PostgreSQL + pgvector                                           |
| Queue          | Redis + Celery (beat for scheduling)                            |
| Scraping       | httpx, BeautifulSoup/lxml                                       |
| Embeddings     | Voyage (REST, no SDK)                                           |
| Reranking      | Voyage (REST, no SDK)                                           |
| Notifications  | Telegram Bot API; provider interface for future channels        |
| Infra          | Docker, Docker Compose, Caddy                                   |
| Quality        | pytest, ruff, mypy --strict                                     |

## Module map

```text
backend/app/
    api/               FastAPI routers — HTTP only, no business logic
    domain/            Framework-free business logic
        candidates/      CvDocument, UserPreference
        jobs/            Raw/Normalized/Canonical, dedup, scrape rotation
        matching/        documents (what the models read), filters, scoring, models
        notifications/   delivery policy
        pipeline_config  every tunable, with its own description and bounds
    services/          Use-case orchestration
        embedding_service    keeps the vector index current
        matching_service     one matching pass for one user
        system_service       status snapshot + reset actions
    repositories/      Persistence, one per table group
    integrations/
        sources/         JobSourceAdapter implementations (dou/, djinni/)
        voyage.py        embed + rerank, the whole of this app's AI
        notifications/   NotificationProvider implementations
    workers/           Celery app + tasks (pipeline, notify, retention)
    db/                SQLAlchemy models + session management
    config/            Settings (infrastructure only, env-driven)
```

## Adding a new job source

1. Implement `JobSourceAdapter` (`backend/app/integrations/sources/base.py`).
2. Register it in `backend/app/integrations/sources/registry.py`.
3. Add its category list to `backend/app/integrations/sources/categories.py`.
4. Done — embedding, matching, notifications and the UI don't change.

## Where to read next

- [docs/pipeline.md](docs/pipeline.md) — the four steps, the scoring formula, and
  every setting.
- [docs/domain-model.md](docs/domain-model.md) — entities and why the boundaries
  sit where they do.
- [docs/source-adapters.md](docs/source-adapters.md) — the adapter contract.
- [docs/notifications.md](docs/notifications.md) — delivery policy and the card.
- [docs/deployment.md](docs/deployment.md) — running it for real.
