# Domain model

## Core entities

```text
User

CandidateProfile
CVDocument
CandidateSkill
UserPreference

Source
SearchProfile
ScrapeRun

RawJob
CanonicalJob
JobSourceRecord
JobSkill
JobRequirement
JobVersion

Embedding

JobMatch
MatchReason
MatchGap

UserJobAction
Application

Notification
NotificationDelivery

Feedback
```

## Relationships

```text
User
 │
 ├── CandidateProfile
 │      ├── CandidateSkill
 │      └── CVDocument
 │
 ├── UserPreference
 │
 ├── SearchProfile
 │
 ├── JobMatch ───── CanonicalJob
 │                    │
 │                    ├── JobSkill
 │                    ├── JobRequirement
 │                    ├── JobVersion
 │                    │
 │                    └── JobSourceRecord ─── Source
 │
 ├── Application
 ├── Feedback
 └── Notification
```

## Raw → Normalized → Canonical

A scraped vacancy passes through three shapes, and no downstream module is allowed to
skip a stage:

1. **`RawJob`** — exactly what the adapter fetched (e.g. raw HTML, source id, url).
   Nothing is thrown away here, so a parser bug is always recoverable by re-running
   normalization against stored raw payloads.
2. **`NormalizedJob`** — source-independent shape: title, company, description,
   employment type, location, salary, seniority, required experience, skills. Matching,
   dedup and notifications only ever see this shape or later.
3. **`CanonicalJob`** — the deduplicated, single real-world vacancy, which may be backed
   by more than one `JobSourceRecord` (the same job posted on DOU *and* Djinni).

This separation means a parser rewrite (source HTML changed) never touches matching
logic, and a matching algorithm change never touches scraping.

## Candidate profile vs. user preferences

Two distinct models that must never be merged:

- **`CandidateProfile`** — what the candidate has actually done: experience years,
  roles, skills (with level/years), work history, achievements, domains, AI experience.
  Derived from parsed CVs, refinable by hand.
- **`UserPreference`** — what the candidate wants: desired salary, preferred/acceptable/
  blocked stack, work format, locations, max required experience the candidate is
  willing to apply beyond, industry/company blacklist.

`CandidateProfile` answers "can I do this job?"; `UserPreference` answers "do I want
this job?" — the matching engine scores both independently (see
[matching-engine.md](matching-engine.md)).

## Skill ontology

Skills need a registry, not free-text strings, so that `"JS"`, `"Javascript"` and
`"JavaScript"` collapse to one skill, and related-but-distinct skills (`Django` →
`FastAPI`, `NestJS`) can carry a transferability weight instead of counting as a flat
miss. See `backend/app/domain/candidates/skills.py` for the `SkillRegistry` contract and
[matching-engine.md](matching-engine.md) for how transferability feeds scoring.

## Multiple CV profiles

A candidate may keep more than one CV variant (e.g. `fullstack`, `frontend`,
`ai_fullstack`). Each vacancy is scored against every variant, and the platform
recommends whichever CV scores highest for that specific job.

## Change detection

A `CanonicalJob` is versioned (`JobVersion`) so re-scraping the same vacancy can detect
`NEW` / `UPDATED` / `CLOSED` / `REOPENED` transitions (e.g. a salary range changing),
not just "new vs. already seen."
