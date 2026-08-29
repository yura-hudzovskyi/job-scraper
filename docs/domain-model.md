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

## Skill identity

Skills are free text end to end, never normalized against a fixed vocabulary — a
hand-maintained skill registry was tried and dropped, since it only ever covered a
narrow slice of what real postings mention and silently defaulted unrecognized jobs
to a perfect score. Instead, `"JS"`/`"Javascript"`/`"JavaScript"` (or `Django` vs
`FastAPI`) are treated as "the same skill" by embedding cosine similarity — no
canonicalization step needed. See
`backend/app/domain/matching/skill_matching.py`'s `SkillMatcher` and
[matching-engine.md](matching-engine.md) for how that similarity feeds scoring.

## Multiple CV profiles

A candidate may keep more than one CV variant (e.g. `fullstack`, `frontend`,
`ai_fullstack`). Each vacancy is scored against every variant, and the platform
recommends whichever CV scores highest for that specific job.

## Change detection

A `CanonicalJob` is versioned (`JobVersion`) so re-scraping the same vacancy can detect
`NEW` / `UPDATED` / `CLOSED` / `REOPENED` transitions (e.g. a salary range changing),
not just "new vs. already seen."
