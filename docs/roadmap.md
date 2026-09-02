# Roadmap

What is built, what was deliberately left out, and what comes next.

## Done

**Foundation** — FastAPI + Postgres/pgvector + Redis + React via Docker Compose.
CV upload, preferences, the DOU and Djinni adapters, raw storage, normalization,
cross-source deduplication, and a category rotation so scraping reaches every
category over time.

**Matching** — one Voyage vector per vacancy and per CV, pgvector cosine search,
hard filters, Voyage reranking over the top of the results, and a score that is
the weighted blend of those two signals. Every match stores both inputs and the
weight, and the UI shows the arithmetic.

**Telegram** — swipe cards with real Approve/Reject buttons via webhook,
idempotent delivery, quiet hours, a per-user score threshold.

**Operations** — a System page that reports readiness and its blockers, runs the
whole pipeline on demand, records what every step did, edits every tunable, and
resets any part of the data with a per-table count of what it deleted.

## Deliberately not built

**An LLM layer.** Per-requirement gap analysis, "should I apply?" write-ups, cover
letters and CV-variant advice all need a language model to be honest, and an
earlier version of this app had one. It was removed: the extraction it depended on
was the least reliable part of the system, and a confident-looking verdict built on
a bad skill list is worse than no verdict. Adding it back is a new step after
reranking, not a rewrite — but it should arrive with an evaluation set, not before
one.

**A daily digest.** The notification policy is one threshold and quiet hours. A
digest tier existed as a config field nobody could see the effect of, and was
removed rather than left half-built.

## Still ahead, roughly in priority order

- **Application tracker** — discovered → applied → interview → offer/rejected, and
  the conversion analytics that only become possible once it exists.
- **An evaluation set.** A few hundred labelled (CV, vacancy) pairs would turn
  `rerank_weight`, `retrieval_limit` and the score thresholds from defensible
  guesses into measured values, and is the prerequisite for taking any further
  ranking change seriously.
- **Missing-skill analytics** — "learning NestJS unlocks ~18% more high-fit jobs",
  computed from the corpus rather than asserted by a model.
- **More sources.** The adapter contract is the point; adding one should not touch
  matching or notifications.
