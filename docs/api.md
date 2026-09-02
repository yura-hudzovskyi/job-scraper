# API surface

REST, served by FastAPI under `backend/app/api/`. Routers are thin — they validate
input and delegate to `services/`; no business logic lives in a route handler.

```text
POST   /api/auth/register
POST   /api/auth/login
GET    /api/auth/me

POST   /api/cv                    upload; the newest CV is the active one, and uploading
                                   one re-matches every vacancy in the background
GET    /api/cv
GET    /api/cv/active             the active CV plus the exact document the models are given
DELETE /api/cv/{id}

GET    /api/profile               read-only onboarding summary (CVs on file, preferences set)

GET    /api/jobs                  paginated (?limit, ?offset); each item carries its match —
                                   score, similarity, relevance, the models that produced them.
                                   Hides skipped and filtered-out vacancies unless
                                   ?include_skipped=true
GET    /api/jobs/{id}             the vacancy plus `model_document`: the exact text the
                                   embedding and rerank models were given for it
POST   /api/jobs/rematch          re-run search + rerank for this user against what is
                                   already stored (no scraping)

GET    /api/sources               per-source raw counts and the categories it rotates through
GET    /api/sources/runs          recent scrape runs: seen, new, errors

GET    /api/settings              user preferences
PATCH  /api/settings              saving re-matches in the background — preferences change
                                   both the filters and the query the models see
GET    /api/settings/notifications
PATCH  /api/settings/notifications

GET    /api/system/status         everything at once: readiness and blockers, counts,
                                   embedding coverage, the full pipeline config with each
                                   setting's description and bounds, the active run and history
GET    /api/system/config
PATCH  /api/system/config         partial update; unknown names and out-of-range values are
                                   rejected rather than ignored
POST   /api/system/config/reset   back to the built-in defaults
POST   /api/system/config/test    one real call against each configured Voyage model

POST   /api/system/run?steps=     full | match | scrape. 409 if a run is already in progress
GET    /api/system/runs           run history with per-step counts

POST   /api/system/reset/notifications
POST   /api/system/reset/matches
POST   /api/system/reset/embeddings
POST   /api/system/reset/jobs
POST   /api/system/reset/all      pipeline data + run history + queued tasks; keeps the account
POST   /api/system/queue/purge    drops tasks no worker has started
POST   /api/system/redis/flush

GET    /api/integrations/telegram/status
GET    /api/integrations/telegram/bot-info    the one shared bot's @username
POST   /api/integrations/telegram/connect     chat_id only — the bot token is server-side
POST   /api/integrations/telegram/test
POST   /api/integrations/telegram/webhook     Telegram calls this, not the frontend. No JWT;
                                               authenticated via X-Telegram-Bot-Api-Secret-Token
```

Every reset endpoint returns what it actually deleted, per table — never a bare
`{"status": "ok"}`. A destructive action should say what it destroyed.

## Frontend pages consuming this API

```text
Dashboard      setup checklist with blockers, corpus/match counts
Jobs           ranked list; every row shows the score and the two signals behind it
Job Details    the score spelled out as the sum it is, plus the exact text the models read
Profile        CV upload, which one is active, and the document built from it
Settings       preferences (what you want vs. rules that filter), notifications, Telegram
Sources        per-source health, category rotation, recent scrapes
System         the pipeline diagram, live status, every setting, run history, reset actions
```
