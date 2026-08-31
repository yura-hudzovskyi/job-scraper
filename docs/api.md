# API surface

REST, served by FastAPI under `backend/app/api/`. Routers are thin — they validate input
and delegate to `services/`; no business logic lives in a route handler.

```text
POST   /api/cv
GET    /api/cv
DELETE /api/cv/{id}               deletes the CV document only — a CandidateProfile already
                                   extracted from it survives (cv_document_id set to null)
POST   /api/cv/analyze

GET    /api/profile

GET    /api/jobs                  paginated (?limit, ?offset), items include practical_fit/recommendation;
                                   excludes Recommendation.SKIP matches by default (?include_skipped=true to see all)
GET    /api/jobs/{id}
GET    /api/jobs/{id}/match
POST   /api/jobs/{id}/rescore
POST   /api/jobs/rescore-all      re-extracts skills + rescores every canonical job for this
                                   user; Gemini-first, falls back to Ollama on rate limit
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
POST   /api/settings/preferences/ai-fill      suggests preferences from the analyzed CV
                                               profile via LLM; returned for review, not saved

GET    /api/integrations/telegram/bot-info    the one shared bot's @username, before connecting
POST   /api/integrations/telegram/connect     chat_id only — the bot token is server-side (TELEGRAM_BOT_TOKEN)
POST   /api/integrations/telegram/test
POST   /api/integrations/telegram/webhook     Telegram calls this, not the frontend — see docs/notifications.md.
                                               No JWT; authenticated via X-Telegram-Bot-Api-Secret-Token instead.
```

`/api/profile` is a read-only summary (CVs on file, whether preferences are set) — it's
derived, not edited. What the candidate *wants* (`UserPreference`) is edited entirely
through `/api/settings`; see docs/domain-model.md.

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
