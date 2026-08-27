# Roadmap

Build in phases. Don't let a coding agent (or yourself) attempt everything at once —
each phase should be independently mergeable and demoable.

## Phase 1 — Foundation

- FastAPI + Postgres + Redis + React, wired together via Docker Compose
- User profile, CV upload, basic settings
- DOU adapter, Djinni adapter (fetch + normalize only)
- Raw job storage, normalization, deduplication

## Phase 2 — Matching

- CV extraction into `CandidateProfile`
- Skill registry
- Hard filters + deterministic scoring
- Local embeddings + pgvector similarity
- Match explanation (component breakdown, strengths/gaps)

## Phase 3 — Telegram

- Bot connection flow
- Instant notifications + daily digest
- Inline actions: save / hide / applied

## Phase 4 — AI

- LLM provider abstraction (Ollama default, OpenAI/Anthropic optional)
- Job requirement extraction
- LLM reranking of the top shortlist
- Gap analysis, "should I apply?"
- CV variant recommendation

## Phase 5 — Job search cockpit

- Application tracker + conversion analytics
- Feedback-driven preference-weight suggestions
- Missing-skill analytics ("learning NestJS unlocks ~18% more high-fit jobs")
- Market skill intelligence (skill demand across relevant jobs)

## Nice-to-haves, roughly in priority order

1. Application tracker
2. Feedback learning loop
3. Multiple CV variants + automatic CV recommendation
4. Cover letter generation
5. Missing-skill analytics
6. Market trends
7. Duplicate vacancy detection across sources
8. Salary extraction/normalization
9. Company blacklist
10. Job freshness/expiry detection
11. Daily/weekly Telegram digest
12. Similar jobs
13. Saved searches
14. Company intelligence

## v1 definition of done

1. CV uploads and is analyzed into a `CandidateProfile`.
2. Profile is manually editable.
3. DOU imports on a schedule.
4. Djinni imports on a schedule.
5. New jobs never duplicate (raw or canonical).
6. Job descriptions are normalized.
7. Skills/requirements are extracted.
8. Every job has a 0–100 match score.
9. Every score has a breakdown explaining it.
10. Both `Requirement Match` and `Practical Fit` are shown.
11. Top jobs are delivered via Telegram.
12. Notifications never duplicate.
13. Telegram supports save/reject/apply actions.
14. The UI shows recommended jobs.
15. One source failing doesn't break the others.
16. A new scraper can be added by implementing `JobSourceAdapter` alone.
17. Matching logic has unit test coverage.
18. Parsers have fixture-based regression tests.
19. Everything starts with `docker compose up`.
