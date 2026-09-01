# Architecture

## Mission

Build a modular, extensible job intelligence platform that aggregates vacancies from
multiple sources, normalizes them into a source-independent domain model, compares them
against structured candidate profiles, ranks them using deterministic rules,
transferable-skill reasoning and semantic similarity, and delivers relevant vacancies
through configurable notification channels.

## Principles

These are load-bearing constraints, not suggestions. Every module should be checked
against them before it's merged.

1. **Adapters, not special cases.** Job sources, LLM providers, embedding providers and
   notification channels are replaceable adapters behind narrow interfaces. Domain logic
   must never import DOU, Djinni, Telegram, OpenAI or FastAPI directly.
2. **Deterministic-primary matching, LLM as a bounded qualitative overlay.** Hard
   filters (candidate-configured, non-negotiable constraints — blacklists, salary
   floor, location, blocked stack) always run first and are never left to an LLM to
   reinterpret. Past that gate, the filters -> weighted-score -> semantic (bi-encoder
   cosine blended with a local cross-encoder reranker) -> skill pipeline is the sole,
   authoritative scorer for every eligible job — no LLM involved, and nothing ever
   overwrites its score. `LlmReranker` ("should I apply?") then layers a qualitative
   verdict on top for CONSIDER+APPLY matches only, capped by a daily call budget — it
   adds judgment (seniority fit, day-to-day realities), never re-derives the score.
   See [docs/matching-engine.md](docs/matching-engine.md).
3. **Explainability is not optional.** Every score ships with a component breakdown,
   strengths, gaps, and critical gaps. A bare number is a bug.
4. **Experience ≠ preference.** What the candidate can do (`CandidateProfile`, derived
   from CVs) is a separate model from what the candidate wants (`UserPreferences`,
   edited directly). See [docs/domain-model.md](docs/domain-model.md).
5. **Raw ≠ normalized ≠ canonical.** Scraped payloads are stored as-is (`RawJob`),
   mapped into a source-independent shape (`NormalizedJob`), then deduplicated into one
   `CanonicalJob` per real-world vacancy. Changing a parser must never require touching
   matching or notification code.
6. **Idempotent background jobs.** Scraping, normalization, embedding, scoring and
   notification delivery all run as background tasks and must be safe to retry:
   `unique(source, external_job_id)`, `unique(user_id, canonical_job_id)`, etc.
7. **One source's failure is not the platform's failure.** A broken Djinni parser
   degrades that adapter's health metric; DOU keeps working; the app keeps running.
8. **Modular monolith over microservices.** One deployable API + one worker pool,
   organized into clean domain modules. Distribute only if a concrete scaling problem
   demands it — not preemptively.
9. **LLMs are reasoning aids, not sources of truth.** Never let an LLM own
   deduplication IDs, job status, deterministic salary parsing, dates, notification
   state, application state, or user preferences. LLMs are for requirement extraction,
   semantic reasoning, transferable-skill reasoning, summarization, reranking, and cover
   letters.

## System overview

```text
┌─────────────────────────────────────────────┐
│                React Web App                 │
└──────────────────────┬───────────────────────┘
                        │ REST API
┌───────────────────────▼───────────────────────┐
│                  FastAPI API                   │
│                                                 │
│ Profile │ Jobs │ Matching │ Applications        │
│ Sources │ Settings │ AI │ Notifications          │
└──────────────┬────────────────────┬─────────────┘
               │                    │
        PostgreSQL              Redis
        + pgvector            Queue/Cache
               │                    │
               │           ┌────────▼────────────┐
               │           │  Background Workers  │
               │           │                      │
               │           │  scrape              │
               │           │  normalize           │
               │           │  embed               │
               │           │  score               │
               │           │  notify              │
               │           └───────┬──────────────┘
               │                   │
         ┌─────▼───────────────────▼──────┐
         │        External Providers      │
         │                                │
         │ DOU │ Djinni │ Telegram        │
         │ LLM │ Embeddings │ future...   │
         └────────────────────────────────┘
```

### Pipeline

```text
Scheduler → FetchSourceJobs → StoreRawJobs → NormalizeJobs → DeduplicateJobs
   → ExtractJobRequirements → CreateEmbedding → RunMatching
   → LLM analysis (top candidates only) → NotificationPolicy → Telegram
```

Every arrow above is a separate, independently retryable background task
(see [docs/roadmap.md](docs/roadmap.md) and `backend/app/workers/`).

## Tech stack

| Layer          | Choice                                                        |
|----------------|-----------------------------------------------------------------|
| Frontend       | React, TypeScript, Vite, TanStack Query, React Router, Tailwind |
| Backend        | Python 3.13+, FastAPI, Pydantic, SQLAlchemy 2, Alembic          |
| Database       | PostgreSQL + pgvector                                          |
| Queue          | Redis + Celery (beat for scheduling)                            |
| Scraping       | httpx, BeautifulSoup/lxml; Playwright only as a JS-required fallback |
| Embeddings     | sentence-transformers (local, default), OpenAI (optional)       |
| LLM            | Provider abstraction — Ollama (default/local), OpenAI, Anthropic (optional) |
| Notifications  | Telegram Bot API first; provider interface for future channels  |
| Infra          | Docker, Docker Compose                                          |
| Testing        | pytest, Vitest, Playwright (E2E)                                 |
| Quality        | ruff, mypy, prettier, eslint, pre-commit                         |

## Module map

```text
backend/
    app/
        api/              FastAPI routers — HTTP only, no business logic
        domain/            Framework-free business logic
            candidates/      CandidateProfile, UserPreferences
            jobs/            RawJob/NormalizedJob/CanonicalJob, deduplication
            matching/        Hard filters, AI matcher (primary), deterministic/embedding scoring (fallback), orchestration
            applications/    Application tracker state machine
            notifications/   Notification policy (thresholds, quiet hours)
        services/          Use-case orchestration between domain + repositories + integrations
        repositories/      Persistence access (behind interfaces, SQLAlchemy underneath)
        integrations/      Everything that talks to the outside world
            sources/         JobSourceAdapter implementations (dou/, djinni/, ...)
            ai/              LLMProvider + EmbeddingProvider implementations
            notifications/   NotificationProvider implementations (telegram/, ...)
        workers/           Celery app + tasks (scrape, normalize, embed, score, notify)
        db/                SQLAlchemy models + session management
        config/            Settings (pydantic-settings, env-driven)
        observability/     Structured logging, metrics hooks
    tests/
        fixtures/          Saved HTML/JSON per source, for parser regression tests
        unit/

frontend/
    src/
        pages/             Dashboard, Jobs, JobDetails, Applications, Profile,
                           MarketInsights, Sources, Settings
        api/               Typed API client
        routes.tsx
```

See [docs/domain-model.md](docs/domain-model.md) for entities and their relationships,
and [docs/source-adapters.md](docs/source-adapters.md) for the adapter contract.

## Adding a new job source

1. Implement `JobSourceAdapter` (see `backend/app/integrations/sources/base.py`).
2. Register it in `backend/app/integrations/sources/registry.py`.
3. Done — matching, dedup, notifications and the UI don't change.

## Roadmap

Phased build order and the v1 definition of done live in
[docs/roadmap.md](docs/roadmap.md). Short version: foundation (profile + two sources +
normalization + dedup) → matching (filters + deterministic score + embeddings) →
Telegram delivery → LLM reranking/extraction → application tracker + analytics cockpit.
