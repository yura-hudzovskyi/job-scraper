# AI pipeline v3: LLM-first enrichment with embedding/reranking fallback

Status: accepted plan, implemented phase by phase (see [Progress](#19-progress)).
Date: 2026-09-01.
Baseline: commit `92b6e87` (clean tree; the earlier uncommitted Ollama-removal work was
reverted, so Ollama removal is part of Phase 0 here).

Backwards compatibility is explicitly **not** a constraint: old rows, old columns and old
code paths may be dropped and rebuilt from scratch whenever that produces a simpler design.

## 1. Executive decision

The system should not choose between an "LLM architecture" and an "embedding architecture".
It should use both, but for different jobs:

- deterministic rules and structured filters remove obvious mismatches;
- embeddings retrieve a broad set of semantically relevant jobs cheaply;
- a reranker orders that set more accurately;
- an LLM performs rich, evidence-backed analysis for the jobs where that analysis creates
  the most value;
- when LLM capacity is unavailable, the same job still receives a complete hybrid result
  built from reranking, normalized skills, experience rules, and score calibration;
- every result records exactly which providers, models, prompts, taxonomies, and fallback
  reasons produced it;
- the UI shows the analysis method and models instead of presenting every score as if it
  came from the same engine.

The target flow is:

```text
CV versions + job versions
        |
one-time structured extraction with evidence
        |
hard filters + high-confidence category gate
        |
embedding retrieval across the best ready index
        |
rerank the candidate set using one model per run
        |
deterministic feature and skill-gap calculation
        |
priority scheduler
        |-- LLM capacity available -> LLM-enriched match
        \-- no LLM capacity        -> hybrid match
        |
calibrated score + confidence + provenance + UI explanation
```

This replaces the previous "AiMatcher or deterministic fallback" branch with two first-class
match engines that implement the same result contract.

## 2. What changes relative to the previous plan

### Keep

- provider adapters;
- N-provider routing;
- Redis-backed quota and cooldown state;
- structured outputs;
- category extraction folded into existing extraction calls;
- once-per-job extraction and caching;
- deterministic matching features;
- the existing local cross-encoder work as an emergency or development fallback;
- completion of the Ollama cleanup.

### Replace or redesign

1. **Do not call the LLM for every category-matched job in arbitrary order.** Retrieve and
   rerank first, then spend scarce LLM capacity on the best or most uncertain jobs.
2. **Do not treat embeddings as a provider leg in the LLM chain.** They return a different
   product and need a different orchestrator branch.
3. **Do not use a hard category mismatch for every classified job.** A wrong category must
   not silently remove a good vacancy. Only high-confidence, clearly incompatible categories
   are a hard gate; the rest are ranking signals.
4. **Do not union multiple CVs and then score against one unspecified profile.** Matches are
   versioned per CV. The system may use the union for discovery, but must select and show the
   best CV for each job.
5. **Do not blend raw reranker scores from different models.** Every rerank run uses one model
   for the entire candidate set and applies a model-specific calibration.
6. **Do not put all embeddings into one vector column without model identity.** Each
   model/version has its own compatible vector space and index lane.
7. **Do not sleep inside a Celery worker to pace requests.** Reserve a provider slot and
   reschedule the task with a countdown so a worker process is not occupied while waiting.
8. **Do not let the LLM invent a final percentage without constraints.** It contributes
   dimension judgments and evidence; the scoring layer produces the comparable final score.

## 3. Architectural principles

### 3.1 Stable domain contracts, replaceable AI implementations

Domain services know about capabilities, not vendors:

```python
class ProfileExtractor(Protocol): ...
class JobExtractor(Protocol): ...
class EmbeddingIndex(Protocol): ...
class RerankEngine(Protocol): ...
class MatchEnricher(Protocol): ...
class MatchScoreCalibrator(Protocol): ...
```

Vendor names such as Gemini, Groq, Voyage, Cohere, Jina, Pinecone, and Cloudflare stay in
integration adapters and policy configuration.

### 3.2 One result contract for both analysis paths

Both LLM and hybrid analysis return the same conceptual dimensions:

```python
class MatchResult(BaseModel):
    profile_id: UUID
    profile_version: int
    canonical_job_id: UUID
    job_version: int

    engine: Literal["llm_enriched", "hybrid"]
    analysis_level: Literal["full", "standard", "limited"]

    score: int
    confidence: float
    recommendation: Literal["apply", "consider", "skip"]

    dimensions: MatchDimensions
    matched_skills: list[SkillEvidence]
    partial_skills: list[SkillEvidence]
    missing_required_skills: list[SkillGap]
    missing_optional_skills: list[SkillGap]
    strengths: list[EvidenceClaim]
    risks: list[EvidenceClaim]

    provenance: MatchProvenance
```

The UI can render either engine without branching into two unrelated pages.

### 3.3 Evidence is required, not decorative

Every extracted skill, inferred equivalence, strength, or gap should point to:

- a CV span;
- a job-description span;
- a normalized taxonomy entity;
- or a deterministic rule identifier.

Claims without evidence are either rejected or shown as low-confidence inferences. This
reduces LLM hallucinations and makes debugging possible.

### 3.4 Results are immutable snapshots

A match is keyed by exact input and engine versions:

```text
profile_content_hash
+ job_content_hash
+ extraction_version
+ taxonomy_version
+ embedding_model_version
+ reranker_model_version
+ matcher_prompt_version
+ scorer_version
```

Changing a CV, job text, prompt, taxonomy, or model creates a new run. Old results remain
explainable.

## 4. End-to-end processing pipeline

### A. CV and job ingestion

#### A1. Persist document versions

Do not overwrite the logical job or CV representation in place.

- `cv_documents` identifies a user document.
- `candidate_profile_versions` stores an immutable extracted version.
- `canonical_jobs` identifies the deduplicated vacancy.
- `job_versions` stores text and extracted fields for each material change.

Minor source changes such as view counters should not invalidate a match. Hash only fields
that affect analysis.

#### A2. Build compact matching documents

Create a stable, provider-neutral text representation instead of sending raw HTML or the full
parsed document to every model.

Candidate document:

```text
TARGET: Full-stack / backend / frontend roles
SENIORITY: middle
YEARS: 3.5
SKILLS: TypeScript 3y; React 2y; Python 0.5y; FastAPI; PostgreSQL
EXPERIENCE:
- Software Engineer, Forex Tester, 2023-present
  chart engine, performance, order management, APIs
LANGUAGES: English B1-B2; Ukrainian native
PREFERENCES: remote; compensation ...
```

Job document:

```text
TITLE: Senior Full-Stack Engineer
CATEGORY: full_stack
MUST: Node.js; TypeScript; PostgreSQL; 5+ years
NICE: AWS; React
RESPONSIBILITIES: architecture; API ownership; UI/backend delivery
CONSTRAINTS: EST hours; remote; US contractor
```

Keep the raw text separately for evidence and LLM review.

#### A3. Extraction strategy

Extraction has a higher quota priority than per-job LLM matching because it is reusable.

Priority order:

1. LLM structured extraction with strict schema and evidence spans.
2. A second LLM leg only on retryable/provider failure or schema-repair failure.
3. Dictionary + aliases + regex + title/category classifier fallback.
4. Raw-text-only representation if extraction is still incomplete.

The rule extractor must cover explicit technology names, years, language levels, location,
work format, employment type, compensation, and common aliases:

```text
React.js / ReactJS        -> react
Postgres / PostgreSQL / PG -> postgresql
Node / Node.js / NodeJS   -> nodejs
Amazon Web Services       -> aws
```

It must not infer implicit skills. Implicit equivalence is handled later by the LLM or
reranker and marked as inferred.

#### A4. Extraction provenance per field

Do not keep only `skills_extracted_by` as one string. A profile can be partially produced by
several methods.

```python
class FieldProvenance(BaseModel):
    method: Literal["llm", "rules", "source", "user"]
    provider: str | None
    model: str | None
    confidence: float
    evidence_span_ids: list[UUID]
```

User corrections outrank every automated extraction and must survive reprocessing.

### B. Filtering and discovery

#### B1. Hard filters

Hard filters should include only facts where a mismatch is genuinely disqualifying and
confidently known:

- explicit excluded country/location;
- impossible work authorization;
- incompatible work format when the user marked it mandatory;
- compensation below a mandatory minimum when salary is present;
- explicitly unacceptable employment type;
- blocked company or already processed job.

Missing information fails open and creates an `unknown` risk, not a rejection.

#### B2. Categories become a confidence-aware gate

The previous pure set-intersection filter is too aggressive.

Use three outcomes:

```python
class CategoryDecision(StrEnum):
    PASS = "pass"
    SOFT_MISMATCH = "soft_mismatch"
    HARD_MISMATCH = "hard_mismatch"
```

- `HARD_MISMATCH` only when both sides have high-confidence categories and the taxonomy says
  they are incompatible.
- `SOFT_MISMATCH` applies a retrieval penalty but leaves the job discoverable.
- missing or multi-role categories produce `PASS` or `SOFT_MISMATCH`, never a hard reject.

Keep an exploration slice of roughly 10% outside the selected categories. This catches
mislabeled and genuinely cross-functional vacancies.

#### B3. Multi-CV handling

Category union is useful only for discovery:

```text
QA CV + DevOps CV -> retrieve QA and DevOps jobs
```

Actual matching must be computed for a specific profile version:

```text
job X
  |-- QA CV score 42
  \-- DevOps CV score 86  <- selected
```

Store `selected_profile_id` on the surfaced job result and show "Matched against DevOps CV"
in the UI. Optionally expose alternative CV scores.

This avoids skill gaps from one CV being incorrectly filled by another CV.

### C. Embedding retrieval

#### C1. Use section-aware embeddings

A single embedding of the entire CV and entire job is simple but loses important structure.
Generate vectors for at least:

- `overview`: title, summary, seniority, domain;
- `skills_requirements`: normalized skills and must/nice-to-have requirements;
- `responsibilities_experience`: responsibilities versus evidence from experience;
- optionally `preferences_constraints`: location, format, compensation, schedule.

Retrieval combines the section similarities with configurable weights. Hard constraints remain
rules, not semantic similarity.

Initial retrieval formula:

```text
0.45 * skills_requirements
+ 0.30 * responsibilities_experience
+ 0.20 * overview
+ 0.05 * preferences_constraints
```

These are initial values only. Tune them on the labeled evaluation set.

#### C2. Multiple embedding lanes, never mixed vector spaces

Embedding fallback is not equivalent to LLM fallback. Vectors from different models are not
directly comparable.

Use versioned lanes:

```text
voyage-4-large:1024:v1
cohere-embed-v4:1024:v1
bge-m3:1024:v1
```

Each lane has:

- its own vectors/index;
- model and provider identity;
- corpus coverage percentage;
- calibration parameters;
- state: `building`, `ready`, `degraded`, `retired`.

The query router chooses the highest-ranked ready lane with sufficient corpus coverage. It
never sends a BGE query vector to a Voyage index.

#### C3. Recommended lane strategy

Maintain two production lanes:

1. **Quality lane:** best API model that passes the domain evaluation.
2. **Durable lane:** an open model available through a recurring-free API and locally with
   compatible weights/tokenizer.

Initial candidates:

| Role | Initial model/provider | Why | Important limitation |
|---|---|---|---|
| Quality | Voyage `voyage-4-large` API | Strong current general-purpose and multilingual model; 32k context; large one-time free token pool | Free pool is not recurring; external API dependency |
| Quality alternative | Cohere `embed-v4.0` API | Multilingual, long context, strong document support | Trial usage is for evaluation, not a production foundation |
| Experimental | Jina `jina-embeddings-v5-text-small` or `jina-embeddings-v4` | Multilingual and long-context; initial free tokens | Treat as non-production until current commercial terms are approved |
| Recurring API | Pinecone `llama-text-embed-v2` or `multilingual-e5-large` | Recurring Starter allowance and managed inference | Pick only after Ukrainian/English CV benchmark; lane is model-specific |
| Durable | Cloudflare `@cf/baai/bge-m3` | Very cheap neuron usage, multilingual, recurring daily allowance | Shared Cloudflare neuron pool can be consumed by LLM traffic |
| Durable local | local BGE-M3 implementation | No external quota and same model family | Verify numerical compatibility with Cloudflare before treating both as one lane; otherwise create two lanes |

Do not permanently encode this table as "truth". `ModelPolicyRepository` holds the ordering,
while an evaluation report decides promotion.

#### C4. Indexing policy

For every new or changed job:

1. build the durable-lane embedding first;
2. enqueue the quality-lane embedding when its budget is available;
3. mark lane coverage atomically;
4. expose a lane for search only after the chosen readiness threshold, normally 99% of active
   jobs;
5. backfill in batches when a new model is introduced;
6. keep the old lane until the new one is evaluated and fully ready.

For a few thousand jobs, two 1024-dimensional float vectors per section are operationally
cheap. The reliability gain is worth the extra storage.

#### C5. Retrieval size

Initial values:

```text
hard/category eligible jobs: all
embedding top K:             100-200 per CV
union across CVs:            deduplicated
exploration candidates:      10-20
```

If the corpus is only about 2,000 jobs, exact pgvector distance may be sufficient initially.
Do not introduce a separate vector database until measurements show Postgres is the
bottleneck.

### D. Reranking pipeline

#### D1. Rerank the compact structured documents

Use the candidate matching document as the query and job matching documents as rerank
documents. Include structured labels such as `MUST`, `NICE`, `YEARS`, and `CONSTRAINTS`;
modern rerankers can use semi-structured text/JSON and this improves requirement awareness.

Rerank only the embedding top K, not the entire job corpus.

#### D2. One model per rerank run

Raw relevance scores are model-specific. Therefore:

- reserve capacity for the complete candidate batch;
- attempt one provider/model for the complete set;
- on failure, discard the partial output and rerun the set through the next model;
- never combine ranks 1-40 from Voyage with ranks 41-100 from Cloudflare;
- transform raw scores through a model-specific calibration before blending.

#### D3. Initial reranker policy

Initial quality-first order, subject to domain evaluation and usage terms:

1. Voyage `rerank-3` - first choice while its free token pool is available.
2. Cohere `rerank-v4.0-pro` - quality evaluation/shadow traffic; use production only with an
   eligible key.
3. Jina `jina-reranker-v3.5` - long-context experimental alternative; production eligibility
   must be confirmed.
4. Pinecone `bge-reranker-v2-m3` - recurring Starter fallback, 500 rerank requests/month.
5. Cloudflare `@cf/baai/bge-reranker-base` - recurring daily fallback and extremely low neuron
   cost.
6. Local `BAAI/bge-reranker-v2-m3` or the existing cross-encoder - quota-free emergency
   fallback.

This is a policy, not a hard-coded `if/elif` chain. A model moves up only after it beats the
current one on the CV/job validation set.

#### D4. Reranker query design

The query should not merely repeat the CV. Add a short invariant instruction:

```text
Rank jobs by realistic fit for this candidate. Prioritize mandatory skills,
evidence of comparable responsibilities, seniority and years. Penalize missing
must-have requirements. Do not reward keyword repetition alone.
```

Version this instruction because it changes ranking behavior.

### E. Deterministic feature and skill-gap engine

Embeddings and rerankers do not extract a reliable list of skill gaps. The fallback path
therefore needs a real feature engine.

#### E1. Skill ontology

Create a versioned ontology with:

- canonical skill id and display name;
- aliases;
- parent/child relationships;
- related but non-equivalent skills;
- technology categories;
- deprecated names;
- optional substitution rules with confidence.

Examples:

```text
FastAPI      -> implies Python web API experience, not general Django experience
React Native -> related to React, not equal to React web
AWS Lambda   -> evidence for AWS, but not for broad AWS architecture
TypeScript   -> strong evidence for JavaScript, not vice versa
```

#### E2. Required versus optional gaps

Job extraction must classify a requirement as:

- required explicit;
- required inferred;
- optional explicit;
- responsibility/context;
- unknown.

The hybrid engine then computes:

```text
matched required
matched through acceptable equivalence
partial/weak evidence
missing required
missing optional
unknown because CV/job evidence is insufficient
```

Never show an unknown as a confirmed missing skill.

#### E3. Experience calculation

Avoid summing overlapping roles or turning a mention into years of experience.

- merge overlapping date intervals;
- keep skill-specific evidence ranges;
- distinguish professional, project, education, and inferred experience;
- cap inferred skill years at the containing role duration;
- store uncertainty for missing dates.

#### E4. Hybrid dimensions

| Dimension | Hybrid evidence |
|---|---|
| Required skills | ontology match + evidence strength |
| Relevant experience | date intervals + responsibility similarity |
| Seniority | title, years, ownership signals |
| Role/domain fit | embedding + reranker |
| Responsibilities | section embedding + reranker |
| Preferences | deterministic fields |
| Risk/unknowns | missing job or CV information |

The hybrid path can therefore return genuine skill gaps and context even without a generative
model. The explanation is template-based from evidence, not generated prose.

### F. LLM-enriched matching

#### F1. LLM is an enrichment and judgment layer

For prioritized jobs, send:

- compact candidate document;
- compact job document;
- extracted evidence and deterministic gaps;
- reranker rank and calibrated probability;
- explicit unknowns;
- strict output schema.

The LLM should review and correct the preliminary analysis, especially:

- implicit transferable experience;
- nuanced responsibility equivalence;
- seniority/ownership signals;
- whether an apparent missing skill is truly a blocker;
- strengths and risks phrased for the user;
- realistic `apply / consider / skip` recommendation.

It must cite evidence ids and may not invent a skill absent from both input documents.

#### F2. LLM matching output

```python
class LlmMatchAssessment(BaseModel):
    dimension_judgments: list[DimensionJudgment]
    confirmed_gaps: list[EvidenceRef]
    downgraded_gaps: list[EvidenceRef]
    transferable_strengths: list[EvidenceClaim]
    risks: list[EvidenceClaim]
    recommendation: Literal["apply", "consider", "skip"]
    confidence: float
```

The scoring service converts these judgments into the final score. The LLM does not output an
unexplained arbitrary percentage.

#### F3. Which jobs receive LLM analysis

Do not process first-come-first-served until the quota dies. Use a value-of-information
priority:

1. user explicitly opens or requests analysis;
2. top reranked unseen jobs;
3. high disagreement between rules, embeddings, and reranker;
4. jobs close to the `apply/consider` decision boundary;
5. newly changed jobs that previously ranked highly;
6. low-value background jobs last.

This gives scarce LLM calls to cases where reasoning can change the user's decision.

#### F4. Quota allocation

Keep separate logical budgets even when capabilities use the same provider account:

```text
profile extraction:      protected reserve
job extraction:          high priority
user-requested analysis: high priority
top-job LLM matching:    medium priority
background enrichment:   low priority
experiments/shadow runs: capped budget
```

Example starting split for a daily LLM token budget:

```text
20% extraction reserve
20% interactive reserve
50% prioritized matching
10% experiments and repair retries
```

Unused reserve can be released near quota reset, but background tasks can never consume the
interactive reserve early.

#### F5. LLM provider routing

Retain an N-provider capability router, but make policy capability-specific:

```text
PROFILE_EXTRACTION: Gemini -> Groq -> Cloudflare
JOB_EXTRACTION:     Groq -> Gemini Flash/Lite -> Cloudflare
MATCH_ENRICHMENT:   Gemini quality model -> Groq strong model -> Cloudflare
SCHEMA_REPAIR:      cheapest structured-output-capable model
```

Exact models live in `model_policies`, not source code. Only providers and models allowed for
the current environment and data classification are eligible.

When every LLM leg is unavailable, `MatchOrchestrator` switches to `HybridMatchEngine`. This is
an orchestration decision, not another provider retry.

### G. Scoring and calibration

#### G1. Comparable score without pretending both engines are identical

The UI needs one sortable score, but an LLM-enriched 84 and a hybrid 84 do not have identical
certainty.

Store and show:

- `score`: calibrated estimated fit, 0-100;
- `confidence`: reliability of the evidence and engine for this case;
- `analysis_level`: full/standard/limited;
- `engine`: LLM-enriched or hybrid.

Never hide `analysis_level` behind the percentage.

#### G2. Initial score composition

Hybrid starting point:

```text
required skills                 30%
relevant experience             20%
reranker calibrated relevance   20%
responsibilities/semantic fit   15%
seniority                       10%
preferences                      5%
```

LLM-enriched starting point:

```text
shared hybrid base             60%
LLM dimension judgments        30%
LLM recommendation consistency 10%
```

Apply deterministic caps/penalties for proven blockers. Example: a confirmed mandatory
work-authorization failure cannot be rescued by a high semantic score.

These weights are hypotheses. The evaluation set must replace them with calibrated values.

#### G3. Calibration

Each engine/model version gets its own mapping from raw values to common fit probability:

```text
raw Voyage rerank score -> calibrated relevance
raw BGE rerank score    -> calibrated relevance
hybrid raw score        -> calibrated match score
Gemini-enriched score   -> calibrated match score
```

Start with isotonic regression or Platt scaling if enough labeled data exists; otherwise use
versioned hand-tuned buckets and explicitly lower confidence.

Do not recalibrate silently. A calibration version invalidates or re-renders affected scores.

## 5. Capability routers and provider state

Replace one generic `ChainLLMProvider` as the architectural center with a shared routing
framework and capability-specific adapters.

```python
class Capability(StrEnum):
    PROFILE_EXTRACTION = "profile_extraction"
    JOB_EXTRACTION = "job_extraction"
    MATCH_ENRICHMENT = "match_enrichment"
    EMBEDDING = "embedding"
    RERANK = "rerank"

class ModelLeg(BaseModel):
    provider: str
    model: str
    priority: int
    environments: set[str]
    data_classes: set[str]
    max_input_tokens: int
    timeout_seconds: float
    enabled: bool
```

### Provider state machine

```text
healthy
  -> repeated timeout/5xx/429
degraded
  -> quota exhausted or circuit threshold
cooldown
  -> reset/probe succeeds
healthy

auth/config error -> disabled + operator alert
```

Classify failures:

- `429` with headers: record exact reset and try next leg;
- daily/monthly quota exhausted: close leg until known reset;
- timeout/5xx: bounded retries, then short circuit cooldown;
- schema-invalid response: one repair attempt, then next capable model;
- 400 prompt too long: deterministic compaction, no blind retry;
- 401/403/bad model id: disable configuration and alert; do not hammer the endpoint.

## 6. Quota, pacing, and job scheduling

### 6.1 Usage ledger

Redis provides fast reservations; Postgres provides durable history.

Track per invocation:

- capability;
- provider/model;
- request id;
- reserved and actual input/output tokens;
- estimated provider units such as neurons or rerank tokens;
- latency;
- status/error class;
- quota window and reset time;
- user/job/profile/run ids;
- cache hit or miss.

### 6.2 Atomic reservation

Before a call:

1. estimate usage;
2. reserve RPM/TPM/RPD/TPD/monthly capacity atomically;
3. if no capacity exists, do not call the provider;
4. reschedule or move to the next policy leg;
5. reconcile estimated versus actual usage after response.

This avoids learning about exhausted quotas only through repeated 429s.

### 6.3 Pacing without blocking workers

The previous GCRA/Lua slot reservation remains useful. Change its consumer behavior:

```text
slot available now -> call provider
slot in 8 seconds  -> Celery retry(countdown=8), release worker
slot after reset   -> schedule at reset + jitter
```

Add small jitter to prevent every worker from waking at exactly midnight UTC.

### 6.4 Backpressure

Use separate queues:

```text
ai_interactive
ai_extraction
ai_matching
ai_backfill
ai_experiments
```

Workers and concurrency can then prioritize user-facing requests without dropping background
work.

## 7. Persistence model

Recommended new or revised tables:

### `ai_model_policies`

Configuration for capability, provider/model order, environment eligibility, timeout, quotas,
and enablement.

### `ai_invocations`

Append-only call ledger with usage, latency, outcome, provider request id, and redacted error
metadata.

### `document_extractions`

Extraction result for one profile/job version, schema version, extractor provenance,
confidence, and evidence spans.

### `document_embeddings`

```text
document_type
document_version_id
section
lane_id
model
dimension
vector
content_hash
created_at
```

Unique key: `(document_type, document_version_id, section, lane_id, content_hash)`.

### `embedding_lanes`

Model identity, vector dimension, index state, corpus coverage, calibration version,
created/retired timestamps.

### `match_runs`

One orchestration run for a profile/job version, engine, score, confidence, recommendation,
analysis level, versions, fallback reason, and full provenance JSON.

### `match_evidence`

Normalized evidence and gap rows used for UI, debugging, and evaluation.

For the first implementation, `provenance` and dimension details may remain JSONB to reduce
migration cost, but `ai_invocations`, `document_embeddings`, and primary match identifiers
should be normalized.

## 8. Caching and idempotency

Cache at capability boundaries:

- extraction by document content hash + schema/prompt/model version;
- embedding by normalized section hash + lane id;
- rerank by profile hash + ordered job hashes + model/instruction version;
- LLM match by profile hash + job hash + evidence hash + prompt/model version;
- final score by component hashes + scorer/calibration version.

Use database uniqueness as the final idempotency guard; Redis locks only reduce duplicate work.

When the same canonical job appears from several sources, analyze the canonical version once
and keep source-specific metadata outside the AI cache key unless it changes requirements.

## 9. UI and API provenance

### 9.1 Job card

Show compact, understandable metadata:

```text
86% match - Full AI analysis
Gemini 3.5 Flash
CV: Full-stack CV - updated 31 Aug
```

Hybrid example:

```text
82% match - Hybrid analysis
Voyage 4 Large + Voyage Rerank 3 + rules
LLM analysis unavailable due to daily quota
```

Do not frame hybrid as a broken result. Explain that it uses semantic retrieval, reranking,
and structured evidence but has less nuanced reasoning.

### 9.2 Result details

Add an "Analysis details" drawer:

| Field | Example |
|---|---|
| Result engine | LLM-enriched |
| Match model | Gemini 3.5 Flash |
| Embedding | Voyage 4 Large |
| Reranker | Voyage Rerank 3 |
| Skill extraction | Groq / model name + rules v4 |
| CV version | Full-stack CV v7 |
| Job version | v3 |
| Taxonomy | skills-v5 |
| Prompt/scorer | match-v4 / score-v3 |
| Generated | timestamp |
| Fallback | none / quota / timeout / provider unavailable |

Provider display names should come from saved provenance, never from current settings.
Otherwise an old result would appear to have been generated by a newly configured model.

### 9.3 API response

```json
{
  "score": 86,
  "confidence": 0.84,
  "recommendation": "apply",
  "engine": "llm_enriched",
  "analysis_level": "full",
  "profile": {"id": "...", "name": "Full-stack CV", "version": 7},
  "models": {
    "embedding": {"provider": "voyage", "model": "voyage-4-large"},
    "rerank": {"provider": "voyage", "model": "rerank-3"},
    "matching": {"provider": "google", "model": "gemini-3.5-flash"}
  },
  "fallback": null,
  "versions": {
    "skill_taxonomy": "5",
    "match_prompt": "4",
    "scorer": "3",
    "calibration": "2"
  }
}
```

Public API errors should not expose provider account ids, rate-limit headers, keys, or
internal stack traces.

## 10. Observability and operations

### Metrics

- eligible jobs before/after each filter;
- retrieval recall and number of exploration results;
- embedding lane coverage and age;
- reranker latency and fallback rate;
- LLM coverage percentage of surfaced jobs;
- hybrid versus LLM score disagreement;
- quota consumption by capability/provider/model;
- cache hit rate;
- schema repair rate;
- user corrections to extracted skills;
- click/apply/save rate by engine and score bucket;
- provider error/cooldown duration.

### Admin UI

The System page should support:

- provider and model enable/disable;
- capability-specific priority;
- current RPM/TPM/daily/monthly remaining estimates;
- next known reset;
- circuit state;
- embedding lane coverage/build progress;
- last successful test;
- model terms/data-policy notes;
- protected budget percentages;
- safe "Test" calls with synthetic data, never a real CV by default.

### Alerts

- no ready durable embedding lane;
- extraction queue older than threshold;
- interactive reserve below threshold;
- auth/config provider failure;
- lane coverage stalled;
- score distribution shifts materially after model/prompt change;
- LLM/hybrid disagreement rate exceeds baseline.

## 11. Privacy and provider eligibility

CVs contain personal data. Add a policy gate before routing:

```python
class DataClass(StrEnum):
    PUBLIC_JOB = "public_job"
    PII_CV = "pii_cv"
    SYNTHETIC_EVAL = "synthetic_eval"
```

Each model leg declares which data classes and environments are allowed. Free tiers or trial
endpoints that allow prototyping but not production, or whose data use is unsuitable for CVs,
must be excluded automatically in production.

Recommended behavior:

- redact phone, email, exact address, and unrelated identifiers before external matching calls;
- keep the candidate id internal;
- send only the compact matching profile unless raw text is required;
- document retention and training/data-use terms per provider;
- allow a user to opt out of external AI and use the durable/local hybrid path;
- do not log raw prompts or CV text in ordinary application logs.

## 12. Evaluation before declaring a model "best"

Public leaderboards are insufficient for this domain. Build a stratified validation set of at
least 200-300 CV/job pairs:

- frontend, backend, full-stack, QA, DevOps, data, support, product;
- English, Ukrainian, and mixed-language texts;
- obvious match, obvious mismatch, and difficult near-boundary cases;
- explicit versus implicit transferable skills;
- multiple CVs for the same user;
- misleading keyword-heavy vacancies.

Label:

- `apply / consider / skip`;
- required/optional skills;
- matched, partial, missing, unknown gaps;
- seniority and experience fit;
- top evidence spans;
- pairwise preference between jobs for the same candidate.

Metrics:

| Layer | Metrics |
|---|---|
| Retrieval | Recall@50/100, NDCG@10, category false-negative rate |
| Reranking | NDCG@10, MAP, pairwise accuracy |
| Skill extraction | precision/recall/F1, evidence accuracy |
| Skill gaps | precision/recall, false blocker rate |
| Recommendation | macro F1, boundary disagreement |
| Score | Brier score, calibration error |
| Operations | p50/p95 latency, tokens, requests, quota coverage |

### Promotion process

1. add a model disabled in production;
2. run offline evaluation;
3. run 5-10% shadow traffic without affecting UI;
4. compare quality, latency, quota burn, and failure rate;
5. promote policy priority if it wins;
6. keep instant rollback to the previous policy/version.

This is why the architecture stores model policy instead of hard-coding "best to worst". The
initial order is a hypothesis; the domain benchmark becomes the truth.

## 13. Detailed implementation plan

### Phase 0 - Stabilize the current baseline

Goal: avoid building the new pipeline on ambiguous uncommitted behavior.

- inventory the current uncommitted files;
- preserve them; do not restore deleted Ollama code;
- finish `.env.example`, Docker, bootstrap, README, architecture, matching, API, and roadmap
  cleanup;
- recover the old `AiMatcher` only into a reference branch/file for behavior comparison, not as
  the final architecture;
- capture current deterministic and cross-encoder outputs for the evaluation set;
- add feature flags: `matching_pipeline_v3`, `llm_enrichment`, `multi_embedding_lanes`.

Acceptance:

- current tests/build pass;
- no runtime Ollama references remain;
- baseline scores and latency are reproducible.

### Phase 1 - Versioned contracts and provenance

Goal: make later provider/model changes observable and safe.

- add immutable profile/job version identifiers and content hashes;
- add `MatchResult`, `MatchProvenance`, evidence, gap, and dimension contracts;
- add `ai_invocations` and `match_runs` migrations;
- persist prompt/schema/taxonomy/scorer/calibration versions;
- return provenance through API and render an initial Analysis Details drawer.

Likely files:

- `app/domain/matching/contracts.py`;
- `app/domain/ai/provenance.py`;
- `app/repositories/ai_invocation_repository.py`;
- `app/repositories/match_run_repository.py`;
- Alembic migrations;
- frontend job API types and details components.

Acceptance:

- an old result continues to show the model that actually generated it after settings change;
- duplicate tasks create one logical match run;
- no raw provider exception is exposed publicly.

### Phase 2 - Extraction v3 and skill ontology

Goal: give both match engines the same structured evidence.

- extend existing job/profile extraction schemas with categories, requirement type,
  confidence, and spans;
- create versioned skill ontology and alias normalizer;
- implement rules fallback;
- preserve user corrections as highest-priority overrides;
- add extraction cache keys;
- build multi-CV discovery union while retaining per-profile matching.

Likely files:

- `app/domain/categories.py`;
- `app/domain/skills/{ontology,normalizer,evidence}.py`;
- `app/services/{cv_service,job_skill_extraction_service}.py`;
- candidate/job repositories and migrations.

Acceptance:

- extraction adds no extra LLM request where an existing call already runs;
- explicit skill precision meets the agreed threshold;
- every automated skill has provenance/evidence;
- user-edited skills survive re-extraction.

### Phase 3 - Capability router and quota manager

Goal: reliable provider use without bursts or quota starvation.

- generalize adapters under a capability router;
- add durable Redis reservation + Postgres usage ledger;
- parse provider reset headers;
- separate transient, quota, schema, and fatal errors;
- add capability budget reserves;
- replace blocking sleeps with Celery countdown rescheduling;
- create interactive/extraction/matching/backfill/experiment queues;
- add provider/model policy settings and admin status.

Likely files:

- `app/integrations/ai/routing/{router,policy,errors}.py`;
- `app/integrations/ai/quota/{manager,pacing,ledger}.py`;
- provider adapters;
- `repositories/ai_settings_repository.py` or new policy repository;
- Celery routing and tasks.

Acceptance:

- a forced 429 uses the exact reset when available and does not keep hitting the provider;
- an auth error disables/alerts instead of falling through silently;
- background work cannot consume the interactive reserve;
- waiting tasks release worker slots.

### Phase 4 - Multi-lane embeddings and retrieval

Goal: fast, high-recall candidate discovery with a durable fallback.

- enable pgvector if not already present;
- create embedding lanes and section vectors;
- implement Voyage quality adapter;
- implement Cloudflare BGE-M3 durable adapter;
- retain Pinecone/Jina/Cohere as pluggable evaluation adapters;
- implement exact/vector retrieval with structured filters and exploration slice;
- add lane backfill, coverage, readiness, and retirement;
- verify Cloudflare/local BGE compatibility before sharing a lane id.

Acceptance:

- no query mixes vector models;
- durable lane covers all active jobs;
- quality lane can be rebuilt without downtime;
- Recall@100 meets the evaluation target;
- jobs outside categories appear in the exploration slice.

### Phase 5 - Reranking chain

Goal: accurately order the retrieved candidate set.

- implement `RerankEngine` contract;
- add Voyage, Pinecone, Cloudflare, and local adapters first;
- add Cohere/Jina behind environment/terms flags;
- rerun complete candidate set on provider fallback;
- add model-specific score calibration;
- persist rerank run provenance and instruction version.

Acceptance:

- partial outputs are never mixed across reranker models;
- fallback ordering is deterministic for the same policy/input;
- NDCG@10 improves materially over embedding-only retrieval;
- raw scores are not shown as match percentages.

### Phase 6 - Hybrid match engine

Goal: produce useful results with zero LLM availability.

- implement required/optional/unknown skill gaps;
- implement experience interval calculations;
- calculate dimensions from rules, embeddings, and reranking;
- add evidence-backed template explanations;
- calibrate hybrid score and confidence;
- replace the old monolithic deterministic fallback with this engine.

Acceptance:

- a full batch completes with all LLM providers disabled;
- hybrid results contain matched skills, gaps, strengths, risks, score, and confidence;
- unknown requirements are not reported as definite gaps;
- each result names embedding/reranker/rules versions.

### Phase 7 - LLM enrichment and priority scheduler

Goal: spend LLM quota where nuanced reasoning matters most.

- implement `LlmMatchEnricher` using the common evidence contract;
- recover useful prompt/schema ideas from old `AiMatcher`, not its orchestration role;
- add value-of-information prioritization;
- add interactive on-demand upgrade from hybrid to LLM;
- reserve and reconcile provider capacity;
- add schema/evidence validation and one bounded repair path;
- calculate calibrated LLM-enriched score.

Acceptance:

- top and uncertain jobs receive LLM analysis before low-value jobs;
- exhaustion switches immediately to hybrid without failing the batch;
- invalid evidence ids reject or downgrade the LLM claim;
- the UI can upgrade an existing hybrid result on demand;
- duplicate LLM analysis is removed.

### Phase 8 - UI completion and operations

Goal: make the architecture visible and controllable.

- add badges for engine and analysis level;
- show exact models and CV version;
- add fallback reason and generated timestamp;
- show component scores and evidence;
- add "Re-analyze with AI" when interactive capacity exists;
- add admin policy/quota/lane screens;
- add metrics, alerts, and dashboards.

Acceptance:

- users can distinguish LLM and hybrid results;
- model attribution survives settings changes;
- admins can see quota reset and lane coverage;
- no secrets or personal raw data appear in UI/logging.

### Phase 9 - Evaluation, rollout, and cleanup

- create the labeled validation set;
- benchmark every initial embedding/reranker/LLM policy;
- run shadow mode;
- roll out v3 to 10%, 50%, then 100%;
- compare click/save/apply behavior and user corrections;
- remove obsolete `LlmReranker` and old fallback code after parity;
- retire old embedding lanes only after rollback window;
- update `ARCHITECTURE.md`, `docs/matching-engine.md`, API docs, deployment docs, and roadmap.

## 14. Focused verification matrix

| Scenario | Expected behavior |
|---|---|
| Groq 429 with reset header | exact cooldown recorded; next eligible LLM leg tried |
| all LLM quotas exhausted | hybrid result produced; fallback reason persisted |
| Voyage reranker fails after partial batch | partial output discarded; entire set rerun by next model |
| quality embedding provider unavailable | ready durable lane selected; no vector mixing |
| new embedding model introduced | separate lane builds in background; old lane serves traffic |
| two CVs have different specialties | discovery uses union; job result identifies best matching CV |
| category is wrong | exploration/soft mismatch can still surface the job |
| job lacks requirements | confidence reduced; unknowns shown; no invented gaps |
| LLM cites nonexistent evidence | claim rejected or confidence downgraded |
| model setting changed | historic result retains original model attribution |
| same task delivered twice | idempotency key returns/reuses the same result |
| Redis unavailable | provider calls remain bounded by safe local limits or task reschedules; ledger recovers |
| external AI disabled by user | durable/local hybrid path only |

## 15. Main compromises and why they are better

### Two embedding lanes instead of a simple fallback chain

Compromise: more vectors, migrations, and backfill logic.

Why better: embedding spaces are incompatible. The extra storage is small at this corpus size,
while a second ready lane gives real provider/quota resilience without corrupt similarity
calculations.

### Retrieve/rerank before LLM instead of making LLM literally first

Compromise: some low-ranked jobs receive hybrid-only analysis even while small LLM capacity may
remain reserved.

Why better: the system spends expensive reasoning on jobs that can realistically matter. It
increases the number of useful LLM analyses per day and prevents quota being wasted on obvious
mismatches.

### Calibrated score plus engine/confidence instead of one "objective" percentage

Compromise: the UI is slightly more complex.

Why better: scores from different models and engines are not naturally comparable. Showing
analysis level and confidence is more honest and makes sorting usable without hiding
uncertainty.

### Soft category filtering plus exploration

Compromise: a few more candidates reach embedding retrieval.

Why better: category classifiers make mistakes and many jobs are cross-functional. A small
compute increase is preferable to silently losing a strong vacancy.

### Per-CV matches instead of a union profile

Compromise: more match records and computation for users with several CVs.

Why better: it prevents experience from unrelated CVs being merged and tells the user which CV
to submit.

### Rules remain part of both engines

Compromise: ontology and rule maintenance are real work.

Why better: embeddings/rerankers cannot reliably extract skill gaps, while LLMs can hallucinate
and vary. Rules provide stable blockers, explicit gaps, and auditability; LLMs add nuance rather
than replacing truth.

### API quality models plus a durable open-model lane

Compromise: the absolute best model is not always used.

Why better: free quotas and terms change. A durable lane prevents the product from becoming
unavailable and provides a controlled degradation path.

## 16. Recommended first production policy

```yaml
extraction:
  protected_budget: true
  profile: [gemini_quality, groq_strong, cloudflare_structured]
  job: [groq_fast, gemini_flash_lite, cloudflare_structured, rules]

embedding_lanes:
  quality:
    model: voyage-4-large
    sections: [overview, skills_requirements, responsibilities_experience]
  durable:
    model: bge-m3
    providers: [cloudflare, local_verified]

retrieval:
  top_k_per_cv: 150
  exploration_count: 15

rerank:
  top_k: 100
  policy: [voyage_rerank_3, pinecone_bge_v2_m3, cloudflare_bge_base, local_cross_encoder]
  experimental_shadow: [cohere_rerank_4_pro, jina_reranker_3_5]

matching:
  llm_priority: [interactive, top_ranked, high_disagreement, decision_boundary, background]
  llm_policy: [gemini_quality, groq_strong, cloudflare_structured]
  fallback: hybrid

ui:
  show_engine: true
  show_models: true
  show_profile_version: true
  show_fallback_reason: true
  show_confidence: true
```

## 17. Current external facts used for the initial policy

These are implementation inputs, not permanent assumptions. Re-check them before rollout:

- Voyage documents `voyage-4-large` as its strongest current general-purpose/multilingual
  embedding model with a 32k context window and lists 200M free embedding tokens per account
  for current Voyage 4 models.
- Voyage currently lists `rerank-3` and `rerank-3-lite` with 200M free rerank tokens per
  account.
- Cohere currently lists multilingual `embed-v4.0`, `rerank-v4.0-pro`, and `rerank-v4.0-fast`;
  trial keys are quota-limited and should be treated as evaluation rather than a production
  foundation.
- Jina currently provides current long-context embedding/reranking models and 10M initial free
  API tokens; production eligibility must be checked against current terms.
- Pinecone Starter currently documents 5M embedding tokens/month/model and 500 rerank
  requests/month for `bge-reranker-v2-m3` and `pinecone-rerank-v0`.
- Cloudflare Workers AI currently provides 10,000 free neurons/day, hosts BGE-M3 and Qwen3
  embedding models, and hosts `bge-reranker-base` at low neuron cost.

Official references:

- https://docs.voyageai.com/docs/embeddings
- https://docs.voyageai.com/docs/pricing
- https://docs.cohere.com/docs/cohere-embed
- https://docs.cohere.com/docs/rerank
- https://docs.cohere.com/docs/rate-limits
- https://api.jina.ai/docs
- https://jina.ai/models/
- https://docs.pinecone.io/reference/api/database-limits
- https://developers.cloudflare.com/workers-ai/platform/pricing/
- https://developers.cloudflare.com/workers-ai/models/

## 18. Final recommendation

Implement phases 0-3 first because contracts, provenance, and quota routing are prerequisites
for every model choice. Then ship the durable embedding lane, reranking, and hybrid engine
before enabling LLM enrichment at scale.

The key product behavior after v3 should be:

1. every eligible job gets a result;
2. the best jobs get richer LLM reasoning first;
3. quota exhaustion changes the analysis method, not product availability;
4. skill gaps still exist in hybrid mode because they come from structured extraction and
   rules, not from embeddings alone;
5. scores remain sortable but never hide which engine and models produced them;
6. model/provider changes are configuration and evaluation work, not another architectural
   rewrite.

## 19. Progress

Each phase lands as a series of small, self-contained commits; the test suite must stay green
at every commit.

- [x] Phase 0 - stabilize the baseline (Ollama removal, feature flags)
- [x] Phase 1 - versioned contracts and provenance
- [x] Phase 2 - extraction v3 and skill ontology
- [ ] Phase 3 - capability router and quota manager
- [ ] Phase 4 - multi-lane embeddings and retrieval
- [ ] Phase 5 - reranking chain
- [ ] Phase 6 - hybrid match engine
- [ ] Phase 7 - LLM enrichment and priority scheduler
- [ ] Phase 8 - UI completion and operations
- [ ] Phase 9 - evaluation, rollout, and cleanup

### Phase 0 notes

Landed: Ollama removed from the backend, the frontend, `docker-compose.prod.yml`,
`deploy/bootstrap.sh` and every doc; the LLM chains are now Groq -> Gemini for the job
pipeline and Gemini -> Groq for CV analysis / preferences AI-fill, with OpenAI/Anthropic
as an optional paid leg behind `LLM_PROVIDER` + `LLM_MODEL`; the Ollama-only per-run
model override on "Rescore all vacancies" is gone (model selection returns as stored
policy in Phase 3); `MATCHING_PIPELINE_V3`, `LLM_ENRICHMENT` and `MULTI_EMBEDDING_LANES`
exist and default to off. Backend suite 181 passed, `ruff` clean, frontend `tsc -b`
clean.

Deliberately not done: the old `AiMatcher` was not copied into a reference file — it is
in git history at `92b6e87^` and can be read from there when Phase 7 wants its prompt.
Capturing baseline scores and latency needs a populated database, so it folds into the
Phase 9 validation set rather than being built twice.

### Phase 1 notes

Landed: `app/domain/versioning.py` (content hashes + `DocumentVersion`), version and
hash columns on `canonical_jobs` and `candidate_profiles`, the `MatchProvenance`
contract with its own JSONB serialization, `job_matches.provenance` replacing the
`scored_by`/`skills_source` strings, provenance returned by `GET /api/jobs/{id}/match`
and rendered as the "Analysis details" drawer, and `LlmCallFailed` so a provider's own
error text never reaches an HTTP response. Backend suite 193 passed, `ruff` clean,
frontend `tsc -b` clean.

Acceptance checked: an old result keeps its attribution because provenance is read back
from the stored payload and never rebuilt from current settings
(`tests/unit/test_provenance.py`); duplicate scoring tasks still produce one row via the
existing `unique(user_id, canonical_job_id)` upsert; a provider exception is logged and
returned as a fixed-message 502 (`tests/unit/test_cv_service.py`).

Deviations from the plan text, all deliberate:

- **No `job_versions` / `candidate_profile_versions` history tables.** Each document
  carries `(version, content_hash)` on its existing row instead. The identity is what
  makes a result explainable; storing the full text of every past revision has no
  consumer yet, and adding the tables later is easy.
- **`ai_invocations` and `match_runs` not created yet.** They land with the code that
  writes them — the usage ledger in Phase 3, orchestrator runs in Phase 6 — rather than
  shipping dead schema now. Until then provenance lives on the match row itself.
- **Models are recorded as provider labels** (`"Groq (llama-3.3-70b-versatile)"`), which
  is what `LLMResult` carries today, not as structured `{provider, model}` pairs. Phase
  3's capability router introduces the structured reference and provenance follows it.
- **`engine` reads `deterministic`.** `hybrid` and `llm_enriched` exist in the enum but
  only start being produced in phases 6 and 7 — reporting today's pipeline as either
  would be a lie in the one place built to stop lying about provenance.

### Phase 2 notes

Landed: the skill ontology and normalizer (`app/domain/skills/`), requirement framing
plus verified evidence quotes, confidence and role category from the one extraction call
that already reads each posting, the rules extractor for when no LLM is available, an
extraction cache keyed on the posting hash plus `EXTRACTION_VERSION`, and user skill
corrections that survive re-extraction (stored per user, re-applied on every analysis,
editable from the Profile page). Backend suite 243 passed, `ruff` clean, frontend
`tsc -b` clean, all four migrations generate valid SQL offline.

Acceptance checked: categories and framing cost no extra request (same schema, same
call); every extracted requirement carries its extractor label, framing, confidence and
a quote verified against the posting; a removed or edited skill survives re-analysis
(`tests/unit/test_cv_service.py`, `tests/unit/test_skill_overrides.py`). "Explicit skill
precision" has no threshold to measure against yet — that needs the labelled set from
phase 9, so it is deliberately unverified rather than declared met.

Deviations from the plan text:

- **Multi-CV discovery union deferred to phase 4.** There is no retrieval step to union
  over yet — every eligible job is still scored for every user — so a "discovery union"
  today would be code with no consumer. Per-CV profiles and `selected_profile_id` land
  with retrieval, where the union is a real operation.
- **CV skills carry `source`, not full field provenance.** `FieldProvenance` with
  provider/model/confidence/evidence spans needs CV text offsets, and nothing consumes
  them until the hybrid engine explains a match in phase 6. Job-side requirements already
  carry evidence quotes today.
- **Categories are extracted but not yet used.** The confidence-aware gate (B2) is phase
  4 work; storing the category now is what makes that gate possible without re-reading
  every posting.
