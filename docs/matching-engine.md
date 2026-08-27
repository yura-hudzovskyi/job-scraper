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

Skill scoring distinguishes `exact match`, `related match` (via the skill ontology),
`missing (nice-to-have)`, and `missing (critical)` — a required skill with no related
match in the candidate's profile costs far more than one with a strong related skill.

**Transferable skill engine:** a framework gap is not the same as a fundamental
engineering gap. `SkillRelation` records a `from → to` transferability weight (e.g.
`django → nestjs: 0.55`) so backend depth in one framework counts toward a related one
instead of scoring as a flat zero.

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

A single question, answered with a structured, explainable recommendation: yes/no,
why, which gaps don't matter (transferable), the main risk, an estimated interview
probability, the recommended CV variant, and a suggested salary ask. This is the
highest-leverage user-facing feature of the matching engine — see
`backend/app/domain/matching/service.py`.

## What the LLM must never own

Deduplication IDs, job status, deterministic salary parsing, dates, notification
delivery state, application state, and user preferences are all deterministic data —
never inferred by an LLM call. LLMs are reserved for requirement extraction, semantic/
transferable-skill reasoning, summarization, reranking, and cover letter generation.
