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

**Phase 4** (LLM reranking, "should I apply?", job requirement extraction) and
**Phase 5** (application tracker) are still interfaces/domain models without business
logic — see [docs/roadmap.md](docs/roadmap.md).

Nothing here has run against a live Postgres yet — Docker Desktop is broken on the
machine this was built on (missing `services.iso`, unrelated to this repo). Every
migration was verified offline instead (DDL compiled from the ORM models diffed
against `alembic upgrade --sql` output) and every external API call was verified live
against the real DOU/Djinni/Anthropic/OpenAI/Telegram endpoints where credentials
allow (auth-rejection paths for the ones needing a key/token this environment doesn't
have).
