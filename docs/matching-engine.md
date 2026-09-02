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

### Running the pipeline

The three stages depend on each other, in this order, and the System page's
Pipeline panel is where they are started and watched:

1. **Extraction + scoring** (`POST /api/ai/pipeline/scoring/run`) — reads every
   posting for its requirements and rescores it. Everything below compares
   against those requirements; a vacancy with none is scored on text similarity
   alone, which is what "analysis level: limited" means on a match.
2. **Embeddings** (`POST /api/ai/pipeline/embeddings/rebuild`) — deletes every
   stored vector and rebuilds. Deliberately destructive: a lane half-filled by a
   previous model, or marked ready when it only covers last month's corpus, is
   harder to trust than an empty one, and vectors are derived data.
3. **Retrieval + rerank** (`POST /api/ai/pipeline/retrieval/run`) — ranks the
   whole corpus for one candidate inside one ready lane, reranks the shortlist,
   and writes the calibrated relevance onto each match. Scoring folds that in on
   its next pass as the role/domain signal, so the ordering ends up in the score
   rather than in a list nobody reads. Which is why step 1 is worth running once
   more after step 3.

Each stage takes a server-side lock (`app/services/pipeline_state.py`) rather
than relying on a disabled button: a second tab or a `curl` call reaches the
endpoint just the same. The locks carry a TTL, so a worker that dies mid-run
can't leave the pipeline "running" forever, and long runs push the expiry out as
they go. Scoring's fan-out has no finish line, so its flag is kept alive by the
work itself and lapses once the queue drains.

### Retrieval and embedding lanes

Scoring every job for every user is affordable only while scoring is cheap.
Reranking, the hybrid engine and LLM enrichment are not, so something has to pick
which vacancies deserve them — that is retrieval
(`backend/app/domain/matching/retrieval.py`), built on section vectors stored per
lane.

**Sections, not blobs.** Both a vacancy and a profile render into the same four
labelled sections (`backend/app/domain/matching/documents.py`): overview,
requirements, responsibilities/experience, constraints. One vector per whole
document averages away the structure that decides fit; sections let requirements
be compared against skills and responsibilities against experience. Retrieval
weights them 0.45 / 0.30 / 0.20 / 0.05 — starting values from
docs/ai-pipeline-v3.md, to be replaced by measured ones in phase 9.

**A lane is one model's vector space.** Vectors from different models are not
comparable, so every stored vector names its lane
(`backend/app/integrations/ai/embeddings/lanes.py`) and a query runs inside
exactly one. Two roles exist: a *quality* lane (Voyage, when configured) and a
*durable* one that is always available — the local sentence-transformers model
needs no key and no quota, which is why retrieval never depends on a hosted
provider being up. A hosted BGE-M3 and a local BGE-M3 would still be separate
lanes until someone verifies the numbers actually match.

**A lane serves queries only when it covers the corpus** (99%, see
`EmbeddingIndexingService.refresh_lane_readiness`). A half-built lane doesn't
return worse results — it returns a smaller world, which is much harder to notice
than an outage, so retrieval skips it and says which lane it did use. Indexing
itself is idempotent: each vector stores the hash of the text it came from, so a
re-scrape that changed nothing costs no provider call.

**The category gate has three outcomes**, not two
(`backend/app/domain/categories.py`):

| Outcome | When | Effect |
|---|---|---|
| `pass` | same category, or either side unclassified | full score |
| `soft_mismatch` | a different but adjacent category (backend vs QA) | ranked down (×0.85) |
| `hard_mismatch` | a confidently-classified different profession (backend vs sales) | out of the main list |

Even a hard mismatch keeps a slot in the **exploration slice** — roughly a tenth
of the result set — because classifiers are wrong and real vacancies are
cross-functional. Ranking something last is recoverable; making it invisible is
not.

Retrieval deliberately does not re-run the hard filters: those live in
`HardFilterService` and need the whole job, and the scoring path already applies
them. Nor does it use an ANN index — at a few thousand jobs an exact pgvector
scan is milliseconds, and the index comes when measurement says so.

### The hybrid engine

`backend/app/domain/matching/hybrid.py` is what the pipeline produces when no LLM
is available, and it is a real answer rather than a degraded one. It runs behind
`MATCHING_PIPELINE_V3`; with the flag off, the pre-v3 weighted scorer is
untouched.

| Dimension | Where it comes from | Weight |
|---|---|---|
| Required skills | share of *required* findings the candidate satisfies (ontology first, embeddings second) | 30% |
| Relevant experience | merged date intervals vs what the posting asks for | 20% |
| Role/domain fit | the reranker's calibrated relevance; semantic similarity stands in when it didn't run | 20% |
| Responsibilities | section similarity | 15% |
| Seniority | the posting's own label vs years actually worked | 10% |
| Preferences | stack preference, pay and place | 5% |

Three properties matter more than the weights, which are hypotheses until phase 9
measures them:

- **A gap is not an unknown.** Only a stated requirement the candidate
  demonstrably lacks is a gap. A skill the posting mentions without framing, or a
  CV whose dates can't be parsed, becomes a *risk* — shown, never counted as
  missing.
- **The score and its certainty are separate numbers.** `confidence` is built
  from what was actually established: how many requirements had a definite
  framing, how many carried an evidence quote, whether a reranker ran, whether
  the CV's dates parsed, whether a model or the rules extractor read the posting.
  A vacancy with nothing extracted still scores — it simply can't claim to have
  checked anything, and says so at 0.25 rather than pretending.
- **Explanations are templated from evidence.** A strength quotes the posting
  line that created the requirement; nothing is generated, so nothing can be
  invented.

Experience is computed rather than trusted
(`backend/app/domain/candidates/experience.py`): extraction models routinely add
up overlapping roles, so intervals are merged, an ongoing role runs to today, and
a skill can't have more years than the roles it appears in. Unreadable dates stay
unknown — they lower confidence and appear as a risk instead of being scored as
missing experience.

### Reranking

Retrieval answers "which hundred vacancies are in the neighbourhood"; a reranker
answers "in what order" (`backend/app/domain/matching/rerank.py`). It reads the
candidate document and each vacancy document *together* rather than comparing two
independently-computed vectors, which is sharper and far too expensive to run
over a whole corpus — so it runs over the retrieved top-K only.

Engines in order (`backend/app/integrations/ai/rerank/`): Voyage `rerank-3`,
Cloudflare's `@cf/baai/bge-reranker-base`, and the local cross-encoder that needs
no key. The last one is why reranking degrades in speed rather than vanishing.

Three rules the implementation enforces:

- **One model per run.** Raw relevance is model-specific, so ranks 1-40 from one
  model and 41-100 from another are not a ranking. A failed *or short* response
  is discarded and the whole set is rerun on the next engine.
- **Deterministic order.** Ties break by canonical job id, so the same input
  ranks the same way and a fallback run is comparable with the run it replaced.
- **Calibration before anyone looks.** Voyage returns a bounded relevance, a BGE
  reranker returns an unbounded logit; `backend/app/domain/matching/calibration.py`
  maps each model's raw score into a comparable 0-1 and records
  `CALIBRATION_VERSION`. These mappings are shaped, not fitted — there is no
  labelled data yet — so anything relying on them carries lower confidence, and a
  raw score is never shown as a match percentage.

The query is not just the CV: a short versioned instruction ("prioritize
mandatory skills, penalise missing must-haves, don't reward keyword repetition")
goes in front of it, because that text changes what a reranker returns.

### LLM enrichment and how it is scheduled

Under `MATCHING_PIPELINE_V3` the scoring path calls no LLM at all. Every match is
scored by the hybrid engine, and a separate pass reviews the ones where a second
opinion could change what the user does
(`backend/app/domain/matching/enrichment.py`,
`backend/app/domain/matching/scheduling.py`).

**What the model is asked.** Not "score this job" — that was the retired
`AiMatcher`, and it doesn't survive a per-job budget. It is shown what the
pipeline found (requirements, gaps, unknowns, dimension scores, the score itself)
and asked what that got wrong: which dimensions should move, which listed gaps
are real blockers, which the candidate's other experience covers, what could
still go wrong in an interview.

**What is done with the answer.** Claims are checked against the inputs — a
confirmed gap must be one of the gaps it was shown, a transferable strength must
name a skill in the posting or the CV, compared through the ontology — and
anything else is dropped and counted. The score stays arithmetic: judgments nudge
dimensions by a bounded step, the same weighted sum runs again, and the result is
60% hybrid base, 30% adjusted dimensions, 10% for the model's recommendation
agreeing with where the score lands. The *label* comes from the score band, never
from the model's own recommendation — that opinion already moved the score once.

**Which matches get reviewed.** Ranked by value of information: proximity to the
apply/consider boundary (a 74 and a 76 mean different advice), disagreement
between the evidence-based and similarity-based views, low confidence, and how
high the score is. A match that already has a verdict is not a candidate at all.
A user pressing "Analyze with AI" on a job page jumps the queue entirely — that
is the strongest signal there is — and lands on the interactive queue.

**Running out of capacity is normal.** The hybrid results stand, nothing is
half-written, and the task comes back when the provider reopens
(`app/workers/pacing.py`).

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
deterministic). Each is a **capability**: call sites ask for "extract a profile", "read a job
posting" or "enrich a match", and a router picks which provider serves it
(`backend/app/integrations/ai/routing/`, wired from
`backend/app/integrations/ai/llm/factory.py`). No call site names a vendor.

The order per capability is policy (`routing/policy.py`), and the same two free
tiers appear in different orders:

- **`PROFILE_EXTRACTION`** — CV analysis and preferences AI-fill. Rare,
  user-triggered, and the one artifact every match a user sees is built on, so it
  leads with the quality model: Gemini, then Groq.
- **`JOB_EXTRACTION`** — reading a posting, once per newly scraped job and again
  on an explicit "rescore all vacancies". It leads with the fast one: Groq runs
  open models on dedicated inference hardware and answers in a second or two,
  which is what makes a real backlog practical.
- **`MATCH_ENRICHMENT`** — the "should I apply?" verdict, currently once per
  CONSIDER+APPLY match. It follows the job pipeline's order rather than leading
  with the quality model, because putting background volume on Gemini first would
  spend the small daily allowance that interactive work depends on.

The optional paid OpenAI/Anthropic leg (`LLM_PROVIDER` + `LLM_MODEL`) comes last
in every chain, and a provider with no credentials is simply absent rather than a
leg that always fails.

**Budgets are per capability** (`app/integrations/ai/quota/budget.py`,
`LLM_DAILY_LIMIT_*`), and separate counters *are* the interactive reserve: a
backlog run burning through job extraction cannot touch what CV analysis has left,
because they never share a budget. The router checks the budget before choosing a
leg, so an exhausted one costs no network round trip.

**Failures are classified, not lumped together**
(`app/integrations/ai/routing/errors.py`). One predicate ("was that a 429?") made
a broken API key look like a rate limit, so it degraded silently forever, and made
a daily quota look like a blip, so it was re-tried every minute until midnight.
Now:

| Kind | What happens |
|---|---|
| `rate_limit` | the leg is parked for exactly what the reset header says (the longer of Groq's two windows), then reopens on its own |
| `quota_exhausted` | parked until the quota really resets — a daily cap's own tiny `retryDelay` hint is not believed |
| `transient` | timeouts and 5xx get a short cooldown |
| `schema` | the provider is healthy and its answer wasn't: one repair attempt on the same leg, then the next leg. Nothing is parked |
| `fatal` | 400/401/403/404 — logged for an operator and parked for half an hour, because retrying a bad key or a retired model id can't help |

Cooldowns live in one Redis store with the reason attached
(`routing/state.py`), which is what lets the System page say *why* a leg is
unavailable and when it comes back.

**When nothing can serve a call**, the router raises `NoCapacity` carrying the
soonest reset instead of returning a silent `None`. Callers differ in what that
should mean: job extraction falls back to the rules extractor and asks its Celery
task to retry when the provider reopens (`app/workers/pacing.py` — countdown from
the provider's own reset, capped and jittered, so the worker slot goes back to the
pool instead of waiting), and matching records `llm_no_capacity` as the match's
fallback reason and keeps its deterministic score.

**Every call is logged** (`app/integrations/ai/quota/ledger.py`): capability,
provider, model, outcome, latency and prompt size go into a capped Redis buffer
that a scheduled task drains into `ai_invocations`. That history is what the
`LLM_DAILY_LIMIT_*` numbers should be tuned against — they start as guesses.

### Changing models at runtime

`GROQ_MODEL` and `GEMINI_MODEL` are `.env` defaults, not the last word — the
System page in the UI can override either of them without a redeploy. `app/config/runtime_settings.py::get_effective_settings`
layers whatever's persisted in Redis (`app/repositories/ai_settings_repository.py`)
on top of the `Settings` instance right before a provider gets built, so a change
saved through the UI takes effect on the very next call. `factory.py` itself never
changes — it still just takes a plain `Settings`. Precedence: the persisted UI
override > the `.env` default.

`POST /api/ai/models/test` fires one real, minimal completion straight at the raw
Groq/Gemini provider (bypassing the router on purpose) and returns
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
top of the `MATCH_ENRICHMENT` capability's own daily budget
(`app/integrations/ai/quota/budget.py`, `LLM_DAILY_LIMIT_MATCH_ENRICHMENT`), which
caps usage independent of whatever the configured provider's own
rate-limit/billing behavior is. Batch reranking over an explicit
shortlist (`rerank_shortlist`) is still deferred — see docs/roadmap.md.

## What the LLM must never own

Deduplication IDs, job status, deterministic salary parsing, dates, notification
delivery state, application state, and user preferences are all deterministic data —
never inferred by an LLM call. LLMs are reserved for requirement extraction, semantic/
transferable-skill reasoning, summarization, reranking, and cover letter generation.
