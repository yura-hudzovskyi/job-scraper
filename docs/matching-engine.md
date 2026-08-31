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

It's wired through `build_configured_llm_provider` (whatever `LLM_PROVIDER` says —
Ollama by default), not through the Gemini-first `build_quality_llm_provider` used
by CV analysis and the reranker below: this call runs once per (job, user) — every
scored job, not just an already-filtered top shortlist — so routing it through
Gemini's reserved free-tier quota would exhaust it fast. See
`backend/app/domain/matching/factory.py`.

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

## LLM provider policy: Gemini for quality, Ollama for volume

Three independent LLM call sites exist — CV analysis
(`backend/app/services/cv_service.py`, user-triggered, rare), job skill extraction
(`backend/app/services/job_skill_extraction_service.py`, once per newly-scraped job,
high-volume, always automatic), and the AI matcher itself (`ai_matcher.py`, once
per (job, user) — the highest-volume of the three).

- **CV analysis, "should I apply?", and AI matching** all go through
  `build_quality_llm_provider`: if `GEMINI_API_KEY` is set, Google's free Gemini
  tier first, falling back to Ollama automatically the instant Gemini returns 429
  (quota exceeded) — but never for other errors, so a misconfigured key fails
  loudly instead of silently degrading. See
  `backend/app/integrations/ai/llm/fallback_provider.py`. Yes, this means AI
  matching — the highest-volume call site — competes for Gemini's quota too;
  see the circuit breaker note below for why that isn't as wasteful as it sounds.
- **Job skill extraction's automatic per-scrape run** always uses Ollama
  unconditionally (`build_bulk_llm_provider`), regardless of Gemini configuration
  — this keeps at least *some* free-tier quota available for the other call
  sites instead of every newly-scraped job burning through it immediately. The
  user-triggered "rescore all vacancies" re-extraction is the exception — it goes
  through `build_quality_llm_provider` like everything else above, since it's an
  occasional explicit action, not automatic per-scrape volume.

**Gemini circuit breaker** (`backend/app/integrations/ai/llm/circuit_breaker.py`):
Gemini's free tier is capped at a small number of requests *per day* — cheap to
exhaust once AI matching is also going through it. Retrying Gemini on every
subsequent call after that would just pay for (and wait on) a network round trip
guaranteed to fail again until the quota resets. `GeminiCircuitBreaker` (Redis,
keyed per model) remembers the first 429 for the rest of that day and makes
`FallbackLLMProvider` skip straight to Ollama for every call after — no proactive
budget/quota-counting needed, this is purely reactive to what Gemini itself
already said no to.

Whichever model actually produced a result is recorded (`LLMResult.model_label`,
`backend/app/integrations/ai/llm/base.py`) and shown in the UI — a Gemini-quota
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
see above) — that's both where the question is actually worth asking and the main
volume control on a personal-scale Gemini
free-tier key, on top of `LlmReranker`'s own daily call budget
(`app/integrations/ai/llm/budget.py`, `LLM_RERANK_DAILY_LIMIT`), which caps
usage independent of whatever the provider's own rate-limit/billing behavior is.
Batch reranking over an explicit shortlist (`rerank_shortlist`) is still
deferred — see docs/roadmap.md.

## What the LLM must never own

Deduplication IDs, job status, deterministic salary parsing, dates, notification
delivery state, application state, and user preferences are all deterministic data —
never inferred by an LLM call. LLMs are reserved for requirement extraction, semantic/
transferable-skill reasoning, summarization, reranking, and cover letter generation.
