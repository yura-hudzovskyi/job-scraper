# Matching engine

The matching engine is **AI-primary, deterministic-fallback**: hard, non-negotiable
filters (blacklists, salary floor, location, blocked stack — things the candidate
explicitly configured) always run first and are never left to an LLM to reinterpret
or hallucinate past. Past that gate, a single structured LLM call (`AiMatcher`,
`backend/app/domain/matching/ai_matcher.py`) decides the actual fit and returns the
full score breakdown as JSON in one shot.

The older filters -> weighted-score -> semantic -> skill pipeline (Stages 2-3 below)
still exists, but only as the **fallback** — used when no LLM is configured, or when
`AiMatcher`'s call fails or comes back with something untrustworthy (timeout,
provider unreachable, malformed output). It never raises; it returns `None` and
`MatchingService.evaluate` falls back automatically, so a scored, explainable
`JobMatch` always comes out the other end either way. Every match records which
path produced it (`JobMatch.scored_by` — `"AI (<model>)"` or `"deterministic"`).

## Pipeline

```text
1000 scraped jobs
   │ hard filters (cheap, deterministic, non-negotiable)
300 eligible candidates
   │ AiMatcher: one structured LLM call → full score + breakdown + recommendation
   │   │ on failure/timeout/no LLM configured, falls back to:
   │   └ deterministic weighted score → semantic similarity → skill matching
300 scored, explainable matches
   │ LLM "should I apply?" (APPLY-tier only, see below)
final ranked list, delivered via notifications
```

### AI matcher (primary path)

`AiMatcher.assess` builds one prompt from the job posting and the candidate's
profile + preferences, and asks for a single structured JSON verdict: the same
`requirement_match` / `practical_fit` / 8-component `breakdown` /
`strengths` / `gaps` / `recommendation` shape the deterministic pipeline produces
(see `_AiVerdict` in `ai_matcher.py`) — the two paths are interchangeable from every
caller's point of view. It never raises out of `assess()`: any exception (timeout,
connection error, a model tag that isn't pulled, a response that fails schema
validation) is caught, logged, and turned into `None` so `MatchingService.evaluate`
falls back to the deterministic pipeline instead of losing the score for that job
entirely.

It's wired through `build_job_llm_provider` — Groq's free tier by default (fast
enough to actually churn through real volume), falling back to a small local
Ollama model on rate limit — not through the Gemini-first
`build_quality_llm_provider` used by CV analysis and preferences AI-fill: this
call runs once per (job, user) — every scored job, not just an already-filtered
top shortlist — so routing it through Gemini's much smaller reserved free-tier
quota would exhaust it in minutes. See `backend/app/domain/matching/factory.py`
and the "LLM provider policy" section below.

### Fallback pipeline

The stages below are unchanged from the original deterministic design and are
already fully explainable on their own (no LLM involved) — they just no longer run
unconditionally.

### Stage 1 — Hard filters

Cheap, configurable reject/pass rules evaluated before any scoring, e.g.:

- relocation required to a country the candidate won't move to → reject
- salary ceiling below the candidate's minimum → reject or penalize
- security clearance / citizenship requirement the candidate can't meet → reject
- required experience far beyond the candidate's → reject
- required stack the candidate has explicitly blocked → reject

Output: `eligible: bool`. See `backend/app/domain/matching/filters.py`.

### Stage 2 — Deterministic score

Weighted components (indicative weights, tunable):

| Component               | Weight |
|--------------------------|--------|
| Skill match               | 30%   |
| Role similarity           | 15%   |
| Semantic similarity       | 15%   |
| Experience level          | 10%   |
| Transferable skills       | 10%   |
| Salary                    | 5%    |
| Location / work format    | 5%    |
| Product/domain relevance  | 5%    |
| User preferences          | 5%    |

Skill scoring distinguishes `exact match`, `related match` (via embedding
similarity), `missing (nice-to-have)`, and `missing (critical)` — a required skill
with no related match in the candidate's profile costs far more than one with a
strong related skill.

Role scoring compares the job title against `UserPreference.preferred_roles` when
the candidate has set it, falling back to `CandidateProfile.roles` (the roles the CV
analysis derived) otherwise — a candidate who never filled in a role preference
still gets a real title-mismatch signal instead of an unconditional 100 for every
job title (`DeterministicScorer._role_score`).

**No extracted skills is not a perfect match.** When a posting has no technical
skills to extract at all (e.g. a non-technical role like "Account Manager"),
`SkillMatcher` has nothing to assess and reports a neutral 100 for
skills/transferable/preferences (nothing required, so nothing missing — see
`SkillMatcher._NEUTRAL`). Folding that straight into the weighted average used to
mean any such job scored ~85%+ against *any* profile, technical or not, because 50%
of the total weight (skills + transferable + preferences) was silently maxed out.
`DeterministicScorer.overall(..., skills_available=False)` instead drops those three
components from the average and rescales the rest, so role/semantic mismatch (the
signals that actually distinguish "wrong profession" from "right profession, no
listed stack") determine the score instead of being drowned out.

**Transferable skill engine:** a framework gap is not the same as a fundamental
engineering gap. Rather than a hand-maintained `from → to` weight table (tried,
dropped — it only ever covered a narrow slice of real postings and silently gave a
perfect score to anything outside its vocabulary), `SkillMatcher`
(`backend/app/domain/matching/skill_matching.py`) embeds each required and each
candidate skill name and uses cosine similarity directly as the transferability
weight — so `django`/`fastapi` naturally score as more related than `django`/`cobol`
without anyone having typed that in.

### Stage 3 — Semantic similarity

Embed the candidate's professional profile and the normalized vacancy
(requirements + responsibilities), then compare with cosine similarity. Local
`sentence-transformers` is the default provider — no API cost for this stage. See
`backend/app/integrations/ai/embeddings/`.

### Stage 4 — "Should I apply?" (APPLY-tier only)

A separate, optional LLM call (`LlmReranker`) layered on top of an already-scored
match — by either path above — for jobs the pipeline recommends `APPLY`. It must
return **structured**, not prose:

```json
{
  "overall_fit": 84,
  "recommendation": "apply",
  "confidence": 0.88,
  "strengths": ["..."],
  "gaps": ["..."],
  "critical_gaps": [],
  "transferable_experience": ["..."],
  "interview_risk": "medium",
  "summary": "...",
  "recommended_cv": "fullstack"
}
```

## LLM provider policy: two tiers, split by volume

Three independent LLM call sites exist — CV analysis
(`backend/app/services/cv_service.py`, user-triggered, rare), preferences AI-fill
(`backend/app/services/profile_service.py`, also user-triggered, rare), job skill
extraction (`backend/app/services/job_skill_extraction_service.py`, once per
newly-scraped job *and* on every "rescore all vacancies" re-extraction), and the
AI matcher itself (`ai_matcher.py`, once per (job, user) — the highest-volume of
all of them). They split into two tiers by volume, each with its own hosted
provider + local fallback + circuit breaker, wired in
`backend/app/integrations/ai/llm/factory.py`:

- **`build_quality_llm_provider`** — CV analysis and preferences AI-fill. Both
  are rare, user-triggered, and quality matters most (CV analysis is the one
  artifact every match a user sees depends on). If `GEMINI_API_KEY` is set, tries
  Google's free Gemini tier first, falling back to `LLM_MODEL` on Ollama the
  instant Gemini returns 429 (quota exceeded) — never for other errors, so a
  misconfigured key fails loudly instead of silently degrading. A
  `GeminiCircuitBreaker` (see below) rides along.
- **`build_job_llm_provider`** — job skill extraction (both the automatic
  per-scrape run and "rescore all vacancies") and the AI matcher — the two
  call sites that run at real volume (once per job, once per (job, user)). If
  `GROQ_API_KEY` is set, tries Groq's free tier first — Groq runs open models on
  dedicated inference hardware, so a response comes back in a second or two
  instead of the CPU-bound minutes a 14B+ model needs under Ollama, which is what
  actually makes processing a real backlog (hundreds of jobs) practical — falling
  back to `OLLAMA_FALLBACK_MODEL` the instant Groq returns 429. With
  `LLM_PROVIDER=ollama` and no `GROQ_API_KEY` at all, this runs on
  `OLLAMA_FALLBACK_MODEL` directly — **not** `LLM_MODEL`. The two are
  deliberately independent: `LLM_MODEL` is what CV analysis/preferences AI-fill
  fall back to, so each call site can run its own local model — e.g. a small,
  fast one here (this leg needs to finish quickly under Celery's concurrent
  load, not be the best quality model available locally) while CV analysis
  keeps a bigger one for its much rarer fallback, or the reverse.
- Both fall back to **`build_configured_llm_provider`** for the `openai`/
  `anthropic` case, where there's no local-vs-hosted distinction to make.

These two tiers deliberately never share a quota: the job pipeline's volume would
exhaust Gemini's much smaller free-tier cap immediately, and CV analysis doesn't
need Groq's speed since it only runs when a user clicks "Analyze."

**Circuit breakers** (`backend/app/integrations/ai/llm/circuit_breaker.py`):
retrying an exhausted provider on every subsequent call pays for — and waits
on — a network round trip that's guaranteed to fail again, so once a 429 is
actually observed, `FallbackLLMProvider` skips straight to the fallback for a
cooldown period instead:

- `GeminiCircuitBreaker` — Gemini's free tier is a *daily* cap, so the cooldown
  runs until the next UTC midnight.
- `FixedCooldownCircuitBreaker` — Groq enforces both per-minute and per-day
  limits, and a 429 during normal use is far more likely to be the former (a
  burst during "rescore all vacancies", not the whole day's quota), so this uses
  a short, fixed cooldown (`GROQ_CIRCUIT_BREAKER_COOLDOWN_SECONDS`) instead of
  parking every later call on the slower Ollama fallback until midnight over
  what was probably transient.

Both are purely reactive to what the provider itself already said no to — no
proactive budget/quota-counting needed on top.

Whichever model actually produced a result is recorded (`LLMResult.model_label`,
`backend/app/integrations/ai/llm/base.py`) and shown in the UI — a quota-driven
fallback to Ollama is never presented as if it were the primary provider.

## Two separate scores

- **Requirement Match** — how literally the CV satisfies the listed requirements.
- **Practical Fit** — how well the candidate could actually do the job, accounting for
  transferable experience.

These are reported separately, not blended into one number.

## Explainability is mandatory

Every score ships with a breakdown — never a bare percentage:

```text
Overall: 84%

Skills             86%
Role                91%
Experience          75%
Semantic fit        88%
Salary              100%
Location            100%

Strong: React, TypeScript, product ownership, performance, APIs
Gaps: NestJS, AWS
Critical: none
```

## "Should I apply?"

A single question, answered with a structured, explainable recommendation: fit
score, apply/consider/skip, confidence, strengths, gaps, which gaps are actually
critical vs. transferable, an interview-risk estimate, a summary, and (when the
candidate has more than one CV variant) which one to use. This is the
highest-leverage user-facing feature of the matching engine — implemented as
`MatchingService.should_i_apply` (`backend/app/domain/matching/service.py`) plus
`LlmReranker` (`backend/app/domain/matching/llm_reranker.py`).

It's deliberately narrow-cast: only called for matches already recommended
`Recommendation.APPLY` (by either the AI matcher or the deterministic fallback —
see above) — that's both where the question is actually worth asking and a
volume control on top of `LlmReranker`'s own daily call budget
(`app/integrations/ai/llm/budget.py`, `LLM_RERANK_DAILY_LIMIT`), which caps usage
independent of whatever the configured provider's own rate-limit/billing
behavior is. Batch reranking over an explicit shortlist (`rerank_shortlist`) is
still deferred — see docs/roadmap.md.

## What the LLM must never own

Deduplication IDs, job status, deterministic salary parsing, dates, notification
delivery state, application state, and user preferences are all deterministic data —
never inferred by an LLM call. LLMs are reserved for requirement extraction, semantic/
transferable-skill reasoning, summarization, reranking, and cover letter generation.
