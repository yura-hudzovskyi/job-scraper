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

Added by Phases 1 and 2 of
[the matching-engine spec](universal-job-matching-system-spec-v1.md). Scraping a
vacancy and uploading a CV now write a revision with its parsed blocks; nothing
*reads* them yet, and matching still works exactly as described above. They exist
so that the extractor arriving in Phase 3 has a versioned, immutable place to
read from, rather than that being retrofitted around live data.

```text
JobSourceRecord ─┐
                 ├── DocumentRevision ── DocumentBlock
CvDocument ──────┘         │              (parsed spans, offsets into parsed_text)
                           ├── DocumentRevisionTransition   (status audit trail)
                           └── ProfileRevision              (extraction output, Phase 3)
ModelRegistry              which model produced what, and when
OutboxEvent                events published in the same transaction as the change
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
  corrected, so a past match stays reproducible. `origin` records who produced
  it — `structural_extraction` and `neural_extraction` are kept apart because
  they fail differently, and `user_override` outranks both for the same field.
  An automated origin must name the extractor behind it, or the profile cannot
  be reproduced.

## Extraction

Runs from the outbox rather than inline with scraping, because it is the step
that will call a model once GLiNER2 lands: off the scrape path, a slow extractor
degrades throughput instead of taking scraping down, and a retry is free because
the revision's state machine records where it got to
(`PARSED → EXTRACTING → EXTRACTED`, or `FAILED` with a reason).

What ships today is the **structural** extractor. It adds no understanding of
its own — it gives values the source adapter already parsed the `Requirement`
shape, an evidence span where the document actually says them, and
`explicit=False` where it does not. It deliberately reads no competencies and no
responsibilities: that is semantic work, it belongs to the model, and inventing
keyword rules for it is what
[the spec](universal-job-matching-system-spec-v1.md) forbids.

`explicit` is the load-bearing flag. Only an explicit requirement may become a
hard filter, because a filter that removes a vacancy has to be able to show the
sentence it removed it for.

Three guarantees hold regardless of which extractor is behind the interface:

- a failure never touches the previous profile — nothing is overwritten, the
  revision goes to `FAILED`, and whatever existed stays;
- an evidence span is re-checked against the revision's stored `parsed_text`
  before the write, because a span can be self-consistent and still point
  somewhere else;
- re-running is safe — a revision not in `PARSED` is skipped, since outbox
  delivery is at-least-once.

**Nothing extracted may influence a match score until the candidate has
reviewed it** (`quality.user_reviewed`, set through
`POST /api/profile/extracted/review`). Phase 7 is where that gate is enforced;
the flag exists now so it can be trusted then rather than retrofitted onto
profiles nobody ever looked at.
- **`ModelRegistry`** records both kinds of model the spec uses — retrieval
  models called over an API (Voyage), and self-hosted understanding models
  (the Phase 3 extractor). Self-hosted rows must pin a revision; API rows need
  not, because the provider's model name already is the version.

- **`OutboxEvent`** closes the gap between committing a change and publishing an
  event about it: both happen in one transaction, and a relay (`outbox.relay`,
  every minute) moves the event onto the queue afterwards. Delivery is
  at-least-once, so handlers must tolerate a repeat. Nothing consumes
  `document_revision_created` yet — Phase 3's extractor is its first handler.

## What gets parsed, and from what

A vacancy is parsed from its **original markup**, not from
`NormalizedJob.description`. `html_to_text` drops the blank lines that separate
one section from the next, so the flattened form collapses into a single
paragraph and the headings and list items — the structure Phase 3 reads
necessity from — are gone. `NormalizedJob.description_html` carries the markup
through for that reason; `description` remains the field everything else reads.

A CV is parsed as plain text whatever it arrived as, because `extract_text` has
already turned PDF and DOCX into text and there is no markup left. Its section
headings survive only as typography, and
[parsing.py](../backend/app/domain/documents/parsing.py) deliberately declines to
guess headings from typography.

`content_hash` is taken over the **raw** text, never the parsed text.
`parsed_text` is a function of `(raw_text, parser_version)` and both are stored,
so hashing the raw text means improving the parser re-parses existing revisions
instead of manufacturing a new revision for every document in the corpus.

The invariant that makes any of this useful: for every block,
`parsed_text[start_char:end_char] == block.text`. Offsets are built alongside the
canonical text rather than searched for afterwards, and the builder asserts it
before returning.

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
