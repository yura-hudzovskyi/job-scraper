# Domain model

## Entities

```text
User
 ├── CvDocument        uploaded file + extracted text; the newest is the active one
 ├── UserPreference    what the user wants, and the rules that filter vacancies out
 ├── JobMatch ───── CanonicalJob
 ├── TelegramIntegration
 ├── NotificationSettings
 └── Notification ── NotificationDelivery

RawJob ── JobSourceRecord ── CanonicalJob
DocumentEmbedding          one vector per (document, model); document is a job or a user
ScrapeRun                  per source+category record; also the rotation's own state
PipelineConfig             one row, app-wide, edited from the System page
PipelineRun                one row per pipeline run, with per-step counts
```

## Raw ≠ normalized ≠ canonical

Three stages, and collapsing any two of them makes a parser change ripple into
matching:

- **`RawJob`** — the scraped payload, stored exactly as fetched.
  `unique(source, external_id)` makes re-scraping idempotent.
- **`JobSourceRecord`** — that payload mapped into a source-independent shape by
  the adapter's mapper. Also `unique(source, external_id)`.
- **`CanonicalJob`** — one real-world vacancy, deduplicated across sources. A job
  posted on both DOU and Djinni is one canonical job with two source records, and
  the Telegram card links out to both.

Every field on a `NormalizedJob` is parsed deterministically: title, company,
description, employment type, location, salary, seniority, required years.
Nothing on a vacancy is inferred by a model — what a posting asks for is read at
match time, from its text, by the embedding and rerank models.

## Document revisions

Added by Phase 1 of
[the matching-engine spec](universal-job-matching-system-spec-v1.md). **Nothing
writes these yet** — ingestion still works exactly as described above. They exist
so that the extractor arriving in Phase 3 has a versioned, immutable place to
read from and write to, rather than that being retrofitted around live data.

```text
JobSourceRecord ─┐
                 ├── DocumentRevision ── DocumentBlock
CvDocument ──────┘         │              (parsed spans, offsets into parsed_text)
                           ├── DocumentRevisionTransition   (status audit trail)
                           └── ProfileRevision              (extraction output)
ModelRegistry              which model produced what, and when
```

- **`DocumentRevision`** — one immutable version of a source document. A vacancy
  whose text changes gets revision `n+1`; yesterday's text stays byte-identical,
  which is what makes a score computed against it explainable later.
  `unique(owner, content_hash)` makes "the scrape found nothing new" a database
  fact, not an application convention.
- Revisions attach to the **existing** `JobSourceRecord` / `CvDocument` rather
  than to a new identity table. Those two already carry `unique(source,
  external_id)` and the user relationship; a third table naming the same identity
  would be a duplicate, not a layer.
- **`status`** moves through `received → parsed → extracting → extracted →
  indexing → searchable`, and every move is written to
  `DocumentRevisionTransition`. Only `searchable` may be matched on. Illegal
  jumps raise rather than write — a revision that reached `searchable` without
  extracting would match on an empty profile and nothing downstream would notice.
- **`ProfileRevision`** is append-only too, and for the same reason: a user
  correcting their extracted skills creates a new revision pointing at the one it
  corrected, so a past match stays reproducible.
- **`ModelRegistry`** records both kinds of model the spec uses — retrieval
  models called over an API (Voyage), and self-hosted understanding models
  (the Phase 3 extractor). Self-hosted rows must pin a revision; API rows need
  not, because the provider's model name already is the version.

Nothing cascades in the schema, here as everywhere else: revisions are deleted
explicitly before the rows they point at, by `JobRetentionService`,
`SystemService.reset_jobs` and `CvService.delete_cv`.

## Experience ≠ preference

Two separate things, never merged:

- **What the candidate has done** — the `CvDocument`'s text. Embedded and handed
  to the reranker verbatim. Nothing is extracted from it into a structure, so
  nothing can be extracted wrongly.
- **What the candidate wants** — `UserPreference`, typed in directly. It plays
  two distinct roles, and the Settings page separates them because they behave
  very differently:

  | Field | Role |
  |-------|------|
  | `preferred_roles`, `preferred_stack`, `work_formats` | go into the text the models read, as the query |
  | `blocked_stack`, `desired_salary_usd`, `locations`, `companies_blacklist`, `max_required_experience` | hard filters — remove vacancies before scoring |

  A field is never both. Putting a constraint into the query as well would apply
  the same fact twice.

## Vectors

`DocumentEmbedding` holds one vector per `(document_type, document_id, model)`:

- `document_type = "job"` → `document_id` is a canonical job id
- `document_type = "profile"` → `document_id` is a user id

The `model` column is load-bearing. Vectors from two models are not comparable,
so every query filters on it; changing the configured model doesn't corrupt the
index, it just leaves the old rows unmatched until they're rebuilt.

`content_hash` is the hash of the exact text the vector came from, which is what
makes re-embedding an unchanged corpus free.

## Matches

A `JobMatch` is deliberately small:

```text
eligible + filter_reasons     did the user's own rules reject it, and which
similarity                    cosine, 0-1
relevance                     reranker, 0-1 — null when it was never reranked
rerank_position               where the reranker put it
score                         the blend, 0-100
recommendation                apply | consider | skip
embedding_model / rerank_model / rerank_weight
decision                      the user's own Approve/Reject, never overwritten by a re-match
```

`score` is always reproducible from the three values above it, which is the whole
point: the UI shows the arithmetic rather than asking for trust. An ineligible
vacancy is still stored — a job missing because of a rule you set is a different
thing from a job that was never seen.
