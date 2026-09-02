# API surface

REST, served by FastAPI under `backend/app/api/`. Routers are thin — they validate input
and delegate to `services/`; no business logic lives in a route handler.

```text
POST   /api/cv
GET    /api/cv
DELETE /api/cv/{id}               deletes the CV document only — a CandidateProfile already
                                   extracted from it survives (cv_document_id set to null)
POST   /api/cv/analyze
GET    /api/cv/profile            the latest analyzed CandidateProfile (no LLM call)
POST   /api/cv/profile/skills     add or correct one extracted skill; the correction is
                                   remembered and re-applied on every later analysis
DELETE /api/cv/profile/skills/{name}
                                  "not one of my skills" — same, as a removal

GET    /api/profile

GET    /api/jobs                  paginated (?limit, ?offset), items include practical_fit/recommendation;
                                   excludes Recommendation.SKIP matches by default (?include_skipped=true to see all)
GET    /api/jobs/{id}
GET    /api/jobs/{id}/match       includes `provenance`: engine, analysis level, CV/job revision,
                                   the models that ran, fallback reason, pipeline versions
                                   (see docs/matching-engine.md#provenance)
POST   /api/jobs/{id}/rescore
POST   /api/jobs/{id}/analyze     asks for an LLM review of this match now, ahead of the
                                   daily ranking (interactive queue)
POST   /api/jobs/rescore-all      re-extracts skills + rescores every canonical job for this
                                   user; Groq-first, falls back to Gemini on rate limit
POST   /api/jobs/{id}/save
POST   /api/jobs/{id}/apply
POST   /api/jobs/{id}/reject

GET    /api/ai/models            model config plus live router state: each capability's
                                   provider chain, why a leg is parked, budget used today,
                                   and embedding lane coverage
GET    /api/ai/pipeline          pipeline state: vacancies, matches, lane coverage, whether
                                   the CV is indexed, and which stages are running now
POST   /api/ai/pipeline/scoring/run        re-extract requirements + rescore every vacancy
POST   /api/ai/pipeline/embeddings/rebuild delete every vector, then re-index everything
POST   /api/ai/pipeline/retrieval/run      rank the corpus by embeddings, rerank the shortlist,
                                   and store the relevance the next scoring pass folds in
GET    /api/ai/usage             LLM calls by capability and outcome over a window, from
                                   the ai_invocations ledger

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
