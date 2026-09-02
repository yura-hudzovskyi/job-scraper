# Job Intelligence Platform

A personal job-search engine: it scrapes vacancies from DOU and Djinni, normalizes
and deduplicates them, ranks them against your CV with embedding search and
reranking, and pushes the good ones to Telegram.

The whole of its AI is **two Voyage API calls** — one turns text into vectors, the
other reads your CV and a vacancy together and scores the fit. There is no LLM
anywhere in it: no extracted skill lists, no generated verdicts, no summaries.
Every score is two numbers and the weight between them, and the UI shows the
arithmetic instead of asking you to trust it.

## How it works

```text
scrape  ->  embed  ->  match  ->  notify
```

One background task, run on a timer and by a button on the System page. See
[docs/pipeline.md](docs/pipeline.md) for each step, the scoring formula, and every
setting.

Everything tunable — models, batch sizes, the rerank weight, score thresholds,
retention — lives in the database and is edited from the System page, with its own
explanation rendered next to it. Only deployment secrets stay in `.env`.

## Repository layout

```text
backend/     FastAPI app, domain logic, source/Voyage/Telegram adapters, Celery worker
frontend/    React + TypeScript web app
docs/        Design docs (pipeline, domain model, adapters, API, deployment)
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — principles, tech stack, module map
- [docs/pipeline.md](docs/pipeline.md) — the four steps, scoring, configuration, resets
- [docs/domain-model.md](docs/domain-model.md) — entities and where the boundaries sit
- [docs/source-adapters.md](docs/source-adapters.md) — the job-source adapter contract
- [docs/notifications.md](docs/notifications.md) — delivery policy and the Telegram card
- [docs/api.md](docs/api.md) — REST API surface
- [docs/deployment.md](docs/deployment.md) — Oracle Cloud free VM, auto-deploy on push

## Getting started

You need a [Voyage API key](https://dashboard.voyageai.com/). Without one the app
still scrapes and stores vacancies, but there is no search and no ranking — and
the System page says exactly that rather than showing an empty list.

```bash
cp .env.example .env          # set VOYAGE_API_KEY, and TELEGRAM_BOT_TOKEN if you want alerts
docker compose up -d          # API, worker, beat, Postgres+pgvector, Redis, web
docker compose exec api alembic upgrade head
```

Then, in the UI:

1. Register, and upload a CV on **Profile**.
2. Set what you're looking for on **Settings** — the first three fields go into
   the query the models see; everything under "Rules" filters vacancies out.
3. Press **Run the whole pipeline** on **System**. It scrapes, embeds, matches and
   notifies, and reports what each step did.

## Local development

The backend runs outside Docker for iterating on it:

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate         # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
pytest                         # unit tests — no DB, no network
ruff check app tests
mypy app                       # strict
```

Running it for real needs Postgres + Redis:

```bash
docker compose up -d postgres redis
alembic upgrade head            # from backend/, with .venv active
uvicorn app.main:app --reload
celery -A app.workers.celery_app worker --loglevel=info
```

Frontend:

```bash
cd frontend && npm install && npm run dev
```

## Status

**Working end to end:** DOU and Djinni scraping (verified against fixtures and the
live sites), normalization and cross-source deduplication, Voyage embedding +
pgvector search, Voyage reranking, hard filters, score bands, Telegram swipe cards
with Approve/Reject via webhook, idempotent delivery, daily retention cleanup, and
a System page that configures and runs all of it.

**Deliberately not built:** an application tracker, a daily digest, and anything
that would need an LLM to be honest — per-requirement gap analysis, "should I
apply" write-ups, cover letters. Adding those means adding a provider back; the
pipeline is shaped so that would be a new step rather than a rewrite.
