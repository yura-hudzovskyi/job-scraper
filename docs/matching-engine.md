# Matching engine

The matching engine is **deterministic-primary**: hard, non-negotiable filters
(blacklists, salary floor, location, blocked stack — things the candidate explicitly
configured) always run first and are never left to an LLM to reinterpret or
hallucinate past. Past that gate, the weighted-score -> semantic -> skill pipeline
(Stages 2-3 below) is the **sole, authoritative scorer** for every eligible job — no
LLM involved, and nothing downstream ever overwrites its `requirement_match`/
`practical_fit`. This used to be a fallback behind a per-job LLM call (`AiMatcher`);
that class has been retired — see "Why deterministic-primary" below.

An optional LLM layer (`LlmReranker`, Stage 4) then adds a *qualitative* verdict —
`JobMatch.llm_assessment` — on top of the already-scored match, for matches the
deterministic pipeline already recommends CONSIDER or APPLY. It never touches the
score itself, only attaches its own independent opinion (fit, gaps, interview risk,
summary) alongside it, gated by a daily call budget. `JobMatch.provenance` records
how the result was actually produced — engine, analysis level, the CV and job
revisions it was scored against, every model involved, and (when the LLM layer
didn't run) why not. See "Provenance" below.

## Why deterministic-primary

The matching engine briefly ran the other way around — a single structured LLM call
(`AiMatcher`) decided the actual fit for every eligible job, with the deterministic
pipeline as its fallback. That doesn't survive real per-job volume on a free-tier
LLM budget: even a fast hosted free tier (Groq, Gemini) has a daily/per-minute cap
that a call-on-every-eligible-job pattern burns through fast. The deterministic pipeline — weighted scoring + skill/
role embeddings + a local cross-encoder reranker (Stage 3) — is fast, free, and
already fully explainable on its own; the LLM's value-add is judgment on *top* of a
trustworthy score (seniority fit, day-to-day realities), not re-deriving the score
itself for every job. `LlmReranker` gated to CONSIDER+APPLY matches (Stage 4) gets
that value at a bounded, predictable call volume instead.

## Pipeline

```text
1000 scraped jobs
   │ hard filters (cheap, deterministic, non-negotiable)
300 eligible candidates
   │ deterministic weighted score → semantic similarity (bi-encoder + cross-encoder)
   │ → skill matching — no LLM, always runs, always authoritative
300 scored, explainable matches
   │ LLM "should I apply?" (CONSIDER+APPLY tier, see below) → llm_assessment overlay
final ranked list, delivered via notifications
```

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

### Extraction and the skill vocabulary

A posting is read once, by one call, which returns every technical requirement it
names *and* how the posting framed each one
(`backend/app/services/job_skill_extraction_service.py`):

| Framing | Meaning | Counts as a gap when missing |
|---|---|---|
| `required_explicit` | stated as a must ("3+ years of X", "must have") | yes |
| `required_inferred` | not labelled a must, but the role can't be done without it | yes |
| `optional_explicit` | nice-to-have, bonus, plus | no |
| `context` | the team's stack or product, not asked of the candidate | no |
| `unknown` | mentioned, with no indication which of the above | no — and never reported as a confirmed gap |

Each skill also carries the verbatim quote that justified its framing, and that
quote is checked against the posting before it is stored: a model that paraphrases
or invents its justification gets no evidence recorded rather than a claim the
vacancy can't back up. The same call classifies the role
(`backend/app/domain/categories.py`) — folding it in is what keeps categories from
costing a second request per job.

Names on both sides — postings and CVs — go through a small hand-kept ontology
(`backend/app/domain/skills/`): canonical ids, aliases, and directed relations
(TypeScript implies JavaScript, never the reverse). It exists next to
embedding-based matching because embeddings can score "same thing?" but cannot
give a skill a stable label to store as evidence, and have no direction. Skills
outside the ontology are not dropped; they simply travel under their own name.

**Without an LLM, extraction still happens.** The rules extractor
(`backend/app/domain/skills/rule_extractor.py`) scans the posting for ontology
aliases and reads the cue words around each mention ("required", "nice to have",
and their Ukrainian equivalents). It finds less, says so through lower confidence,
never produces `required_inferred`, and never overwrites requirements an LLM
already extracted — but a job reaches scoring with real requirements instead of an
empty list that would score as "nothing was checked".

**A posting is not re-read for nothing.** Each extraction records the posting's own
content hash plus `EXTRACTION_VERSION`; a later pass with the same key returns the
stored requirements. "Rescore all vacancies" forces a fresh read, since that is the
point of pressing it.

### User corrections outrank extraction

Skills the user edits on the Profile page are stored per user
(`candidate_skill_overrides`), not inside a profile snapshot, and re-applied to
every analysis: a removal stays removed, an edited level wins over the extractor,
and an added skill appears even though the CV never named it. Each correction
writes a new profile revision — snapshots are immutable, so matches scored against
the previous one keep saying which revision they used, and undoing a removal means
re-analyzing the CV.

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

A second, local signal is blended in on top: a cross-encoder reranker
(`SentenceTransformersCrossEncoderProvider`, `Settings.cross_encoder_model`,
default `cross-encoder/ms-marco-MiniLM-L-6-v2`) jointly scores the
`(profile_text, job_text)` pair instead of comparing two independently-computed
vectors, which is generally sharper for this kind of single-pair relevance
judgment. `SemanticScorer.similarity` blends
`bi_encoder * (1 - weight) + cross_encoder * weight` (`Settings.cross_encoder_weight`,
default `0.5`); set `CROSS_ENCODER_MODEL` blank to disable it and fall back to pure
bi-encoder cosine similarity. Same "load once per worker, no per-request cost"
pattern as the bi-encoder provider — see
`backend/app/integrations/ai/embeddings/cross_encoder_provider.py`. The
domain-mismatch gate's ceilings (`MatchingThresholds.domain_mismatch_*_ceiling`,
see Stage 2) were validated against pure bi-encoder scores — re-check them against
real traffic once the cross-encoder blend has been live for a while.

### Stage 4 — "Should I apply?" (CONSIDER+APPLY)

A separate, optional LLM call (`LlmReranker`) layered on top of an already-scored
match for jobs the deterministic pipeline recommends `CONSIDER` or `APPLY` — never
`SKIP`. It must return **structured**, not prose:

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

Four independent LLM call sites exist — CV analysis
(`backend/app/services/cv_service.py`, user-triggered, rare), preferences AI-fill
(`backend/app/services/profile_service.py`, also user-triggered, rare), job skill
extraction (`backend/app/services/job_skill_extraction_service.py`, once per
newly-scraped job *and* on every "rescore all vacancies" re-extraction), and the
"should I apply?" reranker (`llm_reranker.py`, once per CONSIDER+APPLY (job, user)
match — the highest-volume of all of them, now that scoring itself is fully
deterministic). They split into two tiers by volume, each ordering the same two
free tiers differently, with a circuit breaker on its primary, wired in
`backend/app/integrations/ai/llm/factory.py`:

- **`build_quality_llm_provider`** — CV analysis and preferences AI-fill. Both
  are rare, user-triggered, and quality matters most (CV analysis is the one
  artifact every match a user sees depends on), so this tries Gemini first and
  falls back to Groq the instant Gemini returns 429 (quota exceeded) — never for
  other errors, so a misconfigured key fails loudly instead of silently
  degrading. A `GeminiCircuitBreaker` (see below) rides along.
- **`build_job_llm_provider`** — job skill extraction (both the automatic
  per-scrape run and "rescore all vacancies") and the "should I apply?" reranker
  — the two call sites that run at real volume (once per job, once per
  CONSIDER+APPLY (job, user) match). The same two providers in the opposite
  order: Groq first, since it runs open models on dedicated inference hardware
  and answers in a second or two, which is what makes processing a real backlog
  (hundreds of jobs) practical — falling back to Gemini the instant Groq returns
  429, with a `FixedCooldownCircuitBreaker` riding along.
- Both fall back to **`build_configured_llm_provider`** — the optional paid
  OpenAI/Anthropic leg (`LLM_PROVIDER` + `LLM_MODEL`) — when only one free tier
  is configured, or neither.

The two tiers order the same providers differently rather than isolating quotas
outright: the job pipeline normally stays on Groq and only spills onto Gemini once
Groq's limit trips, so CV analysis usually still finds Gemini's much smaller quota
intact — but a long backlog run can eat into it. `LLM_RERANK_DAILY_LIMIT` and the
circuit breakers keep that bounded; per-capability budget reserves are Phase 3 of
docs/ai-pipeline-v3.md.

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
  parking every later call on the Gemini fallback until midnight over what was
  probably transient.

Both are purely reactive to what the provider itself already said no to — no
proactive budget/quota-counting needed on top.

### Changing models at runtime

`GROQ_MODEL` and `GEMINI_MODEL` are `.env` defaults, not the last word — the
System page in the UI can override either of them without a redeploy. `app/config/runtime_settings.py::get_effective_settings`
layers whatever's persisted in Redis (`app/repositories/ai_settings_repository.py`)
on top of the `Settings` instance right before a provider gets built, so a change
saved through the UI takes effect on the very next call. `factory.py` itself never
changes — it still just takes a plain `Settings`. Precedence: the persisted UI
override > the `.env` default.

`POST /api/ai/models/test` fires one real, minimal completion straight at the raw
Groq/Gemini provider (bypassing `FallbackLLMProvider` on purpose) and returns
either the real model label or the provider's own error text — use it before
committing to a model change, since a model that's wrong, deprecated, or just
rate-limited too tightly for this app's volume degrades *silently* otherwise
(`LlmReranker`/`JobSkillExtractionService` catch every exception and fall back by
design — see above — so a broken `GROQ_MODEL` doesn't fail loudly, it just quietly
never gets used).

**A model that used to work can stop working with no local change at all.**
Concretely: Groq's newer/preview models (`qwen/qwen3.6-27b`, `qwen/qwen3.8-27b`,
`openai/gpt-oss-120b`/`-20b`) run on a much tighter free-tier quota than GA models
like the default `llama-3.3-70b-versatile` — roughly 30 requests/min, 1,000
requests/day, 200K tokens/day per
[console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits)
(Groq's own docs note these can change per-account; check the Limits page in your
own console rather than trusting a number here indefinitely). `LlmReranker`'s
prompt (full job description + candidate profile), run for every CONSIDER+APPLY
match, is large enough that the token budget runs out before the request-count one
does at any real job-pipeline volume — the result is constant 429s falling back to
the Gemini leg, not an obviously "broken" model. `groq_circuit_open`/
`gemini_circuit_open` on `GET /api/ai/models` and `JobMatch.llm_assessment.model_label`
on a real match (recorded whenever the reranker actually ran) are how to tell which
leg is actually serving traffic right now — the score itself is always produced by
the deterministic pipeline, so it never varies by provider.

Whichever model actually produced a reranker result is recorded
(`LLMResult.model_label`, `backend/app/integrations/ai/llm/base.py`, surfaced as
`llm_assessment.model_label` and in provenance) and shown in the UI — a
quota-driven fallback to the other provider is never presented as if it were the
primary one.

## Provenance

Every match carries a snapshot of how it was made
(`backend/app/domain/matching/provenance.py`), stored with the result and returned
by `GET /api/jobs/{id}/match`:

| Field | What it says |
|---|---|
| `engine` | which engine produced it — `deterministic` today; `hybrid`/`llm_enriched` arrive with phases 6 and 7 of docs/ai-pipeline-v3.md |
| `analysis_level` | `full` (an LLM verdict on top), `standard` (scored against extracted requirements), `limited` (nothing extracted to check against, or a hard filter answered first) |
| `profile` / `job` | which revision of the CV and of the posting were scored — `version` plus the content hash from `backend/app/domain/versioning.py` |
| `embedding_model`, `cross_encoder_model`, `skills_model`, `match_model` | the models that actually ran |
| `fallback_reason` | why the LLM layer didn't contribute: `no_llm_provider`, `llm_budget_exhausted`, `below_llm_threshold` |
| `versions` | scorer / match prompt / skill taxonomy / calibration versions in force at the time |

Two rules make this worth storing rather than deriving:

- **it is read back from the row, never rebuilt from current settings** — pointing
  the System page at a different model must not retroactively rewrite who produced
  an old result;
- **the content hash covers only what affects analysis** — re-listing the same
  vacancy under another URL, or re-extracting its skills with a different model,
  is not a new job version; a changed requirement is.

The UI renders this as the "Analysis details" drawer on a job page.

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
`Recommendation.CONSIDER` or `Recommendation.APPLY` (never `SKIP` — see above) —
that's both where the question is actually worth asking and a volume control on
top of `LlmReranker`'s own daily call budget (`app/integrations/ai/llm/budget.py`,
`LLM_RERANK_DAILY_LIMIT`), which caps usage independent of whatever the configured
provider's own rate-limit/billing behavior is. Batch reranking over an explicit
shortlist (`rerank_shortlist`) is still deferred — see docs/roadmap.md.

## What the LLM must never own

Deduplication IDs, job status, deterministic salary parsing, dates, notification
delivery state, application state, and user preferences are all deterministic data —
never inferred by an LLM call. LLMs are reserved for requirement extraction, semantic/
transferable-skill reasoning, summarization, reranking, and cover letter generation.
