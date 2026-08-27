# API surface

REST, served by FastAPI under `backend/app/api/`. Routers are thin — they validate input
and delegate to `services/`; no business logic lives in a route handler.

```text
POST   /api/cv
GET    /api/cv
POST   /api/cv/analyze

GET    /api/profile
PATCH  /api/profile

GET    /api/jobs
GET    /api/jobs/{id}
GET    /api/jobs/{id}/match
POST   /api/jobs/{id}/rescore
POST   /api/jobs/{id}/save
POST   /api/jobs/{id}/apply
POST   /api/jobs/{id}/reject

GET    /api/matches

GET    /api/sources
POST   /api/sources/{id}/sync

GET    /api/search-profiles
POST   /api/search-profiles
PATCH  /api/search-profiles/{id}

GET    /api/applications

GET    /api/settings
PATCH  /api/settings

POST   /api/integrations/telegram/connect
POST   /api/integrations/telegram/test
```

## Frontend pages consuming this API

```text
Dashboard          today's new/recommended jobs, market snapshot, application funnel
Jobs               all / recommended / saved / hidden, with filters
Job Details        score breakdown, strengths/gaps, "should I apply?", original posting
Applications       application tracker (discovered → applied → ... → offer/rejected)
Profile            CV upload/management, candidate profile, preferences
Market Insights    skill-demand analytics, missing-skill opportunities
Sources            per-source health (ScrapeRun history, degraded/healthy)
Settings           matching weights, Telegram connection, search profiles
```
