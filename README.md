# Job Intelligence Platform

A personal job-search engine: it aggregates vacancies from multiple sources, normalizes
them into a source-independent domain model, scores them against a structured candidate
profile, explains every score, and delivers relevant matches through configurable
notification channels (Telegram first).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and
[docs/roadmap.md](docs/roadmap.md) for what's implemented vs. still ahead — in short,
Phase 1 (scraping DOU/Djinni, normalization, dedup, CV upload, preferences) is real;
matching, embeddings, Telegram, and the application tracker are still interfaces/stubs
waiting on their phase.

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

Phase 1 is implemented: the DOU and Djinni adapters really scrape (verified against
both fixtures and the live sites), jobs are normalized/deduplicated and stored in
Postgres, and CV upload / preferences are real, DB-backed endpoints. Matching
(Phase 2), Telegram delivery (Phase 3), LLM reranking (Phase 4), and the application
tracker (Phase 5) are still interfaces and domain models without business logic — see
[docs/roadmap.md](docs/roadmap.md).
