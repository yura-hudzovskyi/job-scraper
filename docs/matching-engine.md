# Matching engine

The matching engine is a **deterministic-first hybrid pipeline**, not
"send CV + vacancy to an LLM and ask for a percentage." That approach is opaque,
expensive at scale, unstable across runs, and impossible to unit test — so it's used
only as a final reranking/reasoning step over a small, already-filtered shortlist.

## Pipeline

```text
1000 scraped jobs
   │ hard filters (cheap, deterministic)
300 eligible candidates
   │ deterministic weighted score
80 candidates above threshold
   │ semantic similarity (embeddings, local by default)
20 top-ranked jobs
   │ LLM reranking + gap analysis (top candidates only)
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

### Stage 4 — LLM rerank (top candidates only)

The LLM only ever sees the shortlist that already survived filters + deterministic +
semantic scoring. It must return **structured**, not prose:

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

Two independent LLM call sites exist outside the scoring pipeline itself — CV
analysis (`backend/app/services/cv_service.py`, user-triggered, rare) and job skill
extraction (`backend/app/services/job_skill_extraction_service.py`, once per
newly-scraped job, high-volume). They deliberately use different providers:

- **CV analysis** — quality matters most here (it's the one artifact every match a
  user sees depends on), and it happens rarely per user, so it can afford a
  higher-quality provider. If `GEMINI_API_KEY` is set, this tries Google's free
  Gemini tier first, falling back to Ollama automatically the instant Gemini
  returns 429 (quota exceeded) — but never for other errors, so a misconfigured key
  fails loudly instead of silently degrading. See
  `backend/app/integrations/ai/llm/fallback_provider.py`.
- **Job skill extraction** — runs on every scraped job, so it always uses Ollama
  unconditionally, regardless of Gemini configuration. This keeps the (limited)
  free-tier quota available for CV analysis instead of being exhausted by volume.

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

It's deliberately narrow-cast: only called for matches the deterministic pipeline
already recommends `Recommendation.APPLY` — that's both where the question is
actually worth asking and the main volume control on a personal-scale Gemini
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
