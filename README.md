# Job Intelligence Platform

A personal job-search engine: it aggregates vacancies from multiple sources, normalizes
them into a source-independent domain model, scores them against a structured candidate
profile, explains every score, and delivers relevant matches through configurable
notification channels (Telegram first).

This repository currently contains **architecture and scaffolding only** — folder
structure, interfaces/contracts, and domain models as stubs. No business logic is
implemented yet. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

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

## Status

Scaffolding stage. Nothing runs end-to-end yet — `docker compose up` will boot the
containers once the stub services grow real implementations.
