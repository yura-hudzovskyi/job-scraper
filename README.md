# Job Intelligence Platform

A personal job-search engine: it aggregates vacancies from multiple sources, normalizes
them into a source-independent domain model, scores them against a structured candidate
profile, explains every score, and delivers relevant matches through configurable
notification channels (Telegram first).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and
[docs/roadmap.md](docs/roadmap.md) for what's implemented vs. still ahead — in short,
Phases 1-3 (scraping, matching, and Telegram delivery) are real; LLM reranking/
"should I apply?" (Phase 4) and the application tracker (Phase 5) are still
interfaces/stubs waiting on their phase.

## Repository layout

```text
backend/     FastAPI app, domain logic, source/AI/notification adapters, Celery workers
frontend/    React + TypeScript web app
docs/        Deep-dive design docs (domain model, matching engine, adapters, API, roadmap)
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — system overview, principles, tech stack, module map
- [docs/domain-model.md](docs/domain-model.md) — entities and their relationships
- [docs/source-adapters.md](docs/source-adapters.md) — job source adapter contract (DOU, Djinni, ...)
- [docs/matching-engine.md](docs/matching-engine.md) — hybrid scoring pipeline
- [docs/notifications.md](docs/notifications.md) — notification channels and delivery policy
- [docs/api.md](docs/api.md) — REST API surface
- [docs/roadmap.md](docs/roadmap.md) — build phases and definition of done
- [docs/deployment.md](docs/deployment.md) — deploying to an Oracle Cloud free VM with auto-deploy on push

## Local development

The backend runs fine outside Docker for iterating on it directly:

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
pytest                        # unit tests — no DB needed
```

Running the API/workers for real needs Postgres + Redis:

```bash
cp .env.example .env
docker compose up -d postgres redis
alembic upgrade head           # from backend/, with .venv active
uvicorn app.main:app --reload  # from backend/
```

`docker compose up` brings up the full stack (API, worker, beat, web) once you also
have the frontend dependencies installed (`cd frontend && npm install`).

## Status

**Phase 1** — the DOU and Djinni adapters really scrape (verified against both
fixtures and the live sites), jobs are normalized/deduplicated and stored in Postgres,
CV upload / preferences are real DB-backed endpoints.

**Phase 2** — CV analysis extracts a structured CandidateProfile via the configured
LLM provider (Anthropic/OpenAI/Ollama, all implemented against their real APIs); a
~65-skill ontology backs deterministic scoring (skills, transferable skills, role,
experience, salary, location, stack preferences) plus real semantic similarity via
local sentence-transformers embeddings; hard filters run first; every match is
explainable (component breakdown, strengths, gaps). Live end-to-end verified without
a database. Persisted embeddings (pgvector) are deferred — on-demand computation is
correct and fine at this scale.

**Phase 3** — NotificationPolicy (score thresholds, quiet hours) gates delivery
through a real Telegram Bot API provider (verified live), with idempotent delivery
tracking so nothing sends twice. `score → notify` is a real Celery chain. Daily-digest
delivery and inline-button (save/applied/reject) callback handling are not built yet —
the buttons render, but tapping them doesn't do anything server-side.

**Phase 4** — job requirement extraction has run since Phase 2 (moved earlier, see
`job_skill_extraction_service.py`). LLM reranking / "should I apply?" is now real
too: `MatchingService.should_i_apply` calls an LLM (Gemini-first with Ollama
fallback, same policy as CV analysis) for matches the deterministic pipeline
already recommends APPLY, gated by a configurable daily call budget
(`LLM_RERANK_DAILY_LIMIT`) independent of the provider's own rate limits — see
`app/domain/matching/llm_reranker.py` and `app/integrations/ai/llm/budget.py`.
Batch reranking over a shortlist (`rerank_shortlist`) is still deferred — no
shortlist view or digest batching exists to feed it yet.

**Phase 5** (application tracker) is still interfaces/domain models without
business logic — see [docs/roadmap.md](docs/roadmap.md).

Docker was broken for most of this build (missing `services.iso`, unrelated to this
repo) — every migration was verified offline instead (DDL compiled from the ORM
models diffed against `alembic upgrade --sql` output), and every external API call
was verified live against the real DOU/Djinni/Anthropic/OpenAI/Telegram endpoints
where credentials allow. Once Docker was fixed, running the real stack against a live
Postgres immediately surfaced three real bugs no amount of offline checking would
have caught: `Settings.api_cors_origins` crashing startup when set from a plain
comma-separated env var, native Windows uvicorn being unable to open an async
Postgres connection at all (psycopg's async mode vs. uvicorn's forced
`ProactorEventLoop`), and asyncpg rejecting timezone-naive timestamp columns against
the app's timezone-aware datetimes. All three are fixed — see the git history for
specifics ("fix: three real bugs found by actually running docker compose up").
