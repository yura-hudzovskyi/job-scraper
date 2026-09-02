# The pipeline

Four steps, run in order, by one background task:

```text
scrape  ->  embed  ->  match  ->  notify
```

The System page runs it on demand and Celery beat runs it on a timer. There is no
second code path for the manual case — the button runs exactly what the schedule
runs, which is what makes "it works when I press it but not on its own"
impossible.

## What the AI actually does

Two calls, both to Voyage, both over one API key:

| Call | Input | Output | Runs over |
|------|-------|--------|-----------|
| **Embed** | one document's text | one vector | every vacancy, once per change; your CV, once per change |
| **Rerank** | your CV + a batch of vacancies | one relevance per vacancy | the top `rerank_top_k` of a search |

That is all of it. Nothing extracts skills, classifies roles, writes a verdict or
produces a summary. There is no LLM in the codebase.

The consequence worth stating plainly: this system is good at *"is this vacancy
in the same neighbourhood as my experience"* and has no opinion at all on *"do I
meet requirement 3"*. Two numbers and the weight between them is the whole claim,
and both numbers are stored on every match so the UI can show the arithmetic.

## Step 1 — scrape

Each run scrapes **one category per source**: whichever has gone longest without
one (`app/domain/jobs/scrape_rotation.py`). Over many runs the rotation reaches
every category, instead of re-reading the same default feed forever.

Listings already known by `(source, external_id)` are skipped before their detail
page is fetched, so a repeated run is cheap. `scrape_max_jobs_per_run` caps how
many *new* listings one run will fetch in full.

Then: raw payload stored as-is → mapped to a source-independent `NormalizedJob` →
deduplicated into one `CanonicalJob` per real vacancy. See
[domain-model.md](domain-model.md).

## Step 2 — embed

Every vacancy becomes **one vector**, from one rendered document
(`app/domain/matching/documents.py`). Not one vector per section: a single
document embedded whole is what the search compares, and it is what the System
page can honestly report coverage for.

Each stored vector carries the hash of the exact text it came from, so a
re-scrape that changed nothing costs no API call. Each also carries the model
that produced it — vectors from two models are not comparable, so changing
`embedding_model` invalidates the whole index and the next run rebuilds it. The
System page reports that as stale vectors rather than letting it look like an
outage.

## Step 3 — match

Per user, in this order:

1. **Embed the CV.** The CV's text plus the parts of your preferences that say
   what you *want* (roles, preferred stack, work format). Constraints are left
   out here on purpose — they're enforced in step 3, and including them would
   apply the same fact twice.
2. **Search.** Cosine similarity over every vacancy vector, keeping the top
   `retrieval_limit`. Anything ranked below that gets no match row at all.
3. **Filter.** Hard rules the user configured — blocked stack, salary floor,
   locations, company blacklist, max required experience. A rejected vacancy is
   **still stored**, with the reason: a job missing because of your own rule is a
   different thing from a job that was never seen.
4. **Rerank.** The top `rerank_top_k` of what survived, in one call. This is the
   only part of a run that costs per document, which is why it comes after the
   filters and not before.
5. **Score.**

   ```text
   reranked:      score = (similarity × (1 - w) + relevance × w) × 100
   not reranked:  score = similarity × 100
   ```

   A vacancy the reranker never saw is **not** penalised for it — the top-K cut
   is about cost, and penalising below it would make a score depend on how many
   vacancies happened to be scraped that week. Its row says `not reranked`.

6. **Band.** `apply_threshold` and `consider_threshold` turn the score into
   apply / consider / skip. Skip and filtered-out vacancies are hidden from the
   jobs list by default.

There is deliberately no calibration layer and no confidence model. There is
nothing here to calibrate against, and a fitted curve would be less honest than
two raw numbers a user can read off the screen.

## Step 4 — notify

Matches at or above the user's `min_score`, outside their quiet hours, are sent
to Telegram as a swipe card with Approve/Reject buttons. Delivery is recorded per
(match, channel), so the same vacancy is never sent twice. See
[notifications.md](notifications.md).

## Configuration

Everything in the table below is edited from the System page and stored in
Postgres (`pipeline_config`, one row). Descriptions and bounds live in
`app/domain/pipeline_config.py` and are rendered by the UI verbatim, so the form
can't document a setting differently from the code that uses it.

| Setting | What it does |
|---------|--------------|
| `embedding_model` | Voyage model for vectors. Changing it re-embeds the corpus. |
| `rerank_model` | Voyage model that reads CV + vacancy together. |
| `scrape_enabled` | Whether a run scrapes at all. |
| `scrape_max_jobs_per_run` | Ceiling on new listings fetched per run. |
| `retrieval_limit` | How many vacancies the search keeps per user. |
| `rerank_top_k` | How many of those the reranker reads. |
| `rerank_weight` | 0 = ignore the reranker, 1 = ignore similarity. |
| `apply_threshold` / `consider_threshold` | Recommendation bands. |
| `job_retention_days` | How long a vacancy survives after it stops being seen. |

Not editable from the UI, by design:

- **API keys and the database URL** — deployment secrets, read from `.env`.
- **`SCRAPE_INTERVAL_SECONDS`** — Celery beat reads its schedule at startup, so
  changing it needs a beat restart. The System page says so rather than offering
  a control that silently wouldn't work.

## Starting over

The System page's reset actions each delete exactly what they name and report the
row counts:

| Action | Deletes | Costs to rebuild |
|--------|---------|------------------|
| Clear notification history | delivery records | nothing |
| Clear matches | matches + their notifications | one rerank pass |
| Clear embeddings | every vector | one embedding pass |
| Clear vacancies | vacancies, matches, notifications, vectors, scrape history | a full run |
| Reset everything | all of the above + run history + queued tasks | a full run |

None of them touch the account: login, CVs, preferences, Telegram connection and
the pipeline config all survive, because "start over" should not mean "set
everything up again".
