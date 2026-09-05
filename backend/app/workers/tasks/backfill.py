"""Bringing documents stored before a stage existed through that stage.

The Phase 1 migration backfilled every vacancy and CV already on file as
revision 1 in `searchable`, which was right at the time: the corpus was being
matched on, and marking it `received` would have made a working system look like
a processing backlog.

The cost of that choice is this task. Those 1775 revisions never went through
parsing, so they have no blocks, no profile and no concept links — the taxonomy
applies only to documents scraped since Phase 3 shipped. Reprocessing them is
what turns "linking works" into "linking has run over the corpus".

Deliberately incremental and re-runnable. It takes a batch size, selects only
revisions with no parsed text, and can be called until the backlog is gone — so
a run that dies halfway loses nothing, and so it never occupies the worker long
enough to delay a scrape.

Not on the beat schedule. The backlog is finite and shrinks to nothing, and a
periodic task whose steady state is "found nothing to do" is a periodic task
nobody will remember the purpose of.

Known limitation, worth stating rather than discovering later. What Phase 1
copied was `job_source_records.description` — already flattened by `html_to_text`,
which drops the blank lines between sections — so these documents parse into a
single paragraph where a freshly scraped one yields twenty-odd headings and list
items. Nothing that runs today reads that structure (StructuralExtractor works
from the adapter's fields, and linking scans the whole text), so the profiles and
concept links these produce are the same ones they would produce either way. It
starts to matter when GLiNER2 lands and necessity is read from section headings.
The fix then is a re-parse from `raw_jobs.payload["html"]`, which still holds the
original detail page for all 1959 of them — not more work here now.
"""

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select, text

from app.config.settings import get_settings
from app.db.models.document import DocumentRevisionModel
from app.db.models.job import JobSourceRecordModel
from app.db.session import session_scope
from app.domain.documents.events import (
    DOCUMENT_REVISION_AGGREGATE,
    DOCUMENT_REVISION_CREATED,
)
from app.domain.documents.language import detect_language
from app.domain.documents.models import EntityKind, RevisionStatus
from app.domain.documents.parsing import ParsedDocument, parse_plain_text
from app.domain.taxonomy.linking import ExtractedSpan
from app.integrations.ml_service import MlServiceClient
from app.integrations.parsers.html import parse_html
from app.repositories.document_repository import DocumentRepository
from app.repositories.mention_repository import MentionRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.taxonomy_repository import TaxonomyRepository
from app.services.concept_linking_service import ConceptLinkingService
from app.services.concept_promotion_service import ConceptPromotionService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

BATCH = 100

# Why a revision was left unparsed, recorded on the row rather than only in the
# log — a log line ages out, and this is the only trace that the document was
# looked at at all.
FAILURE_BACKFILL_PARSE = "backfill_parse_error"


# Closing tags, not opening ones. A vacancy that asks for HTML skills writes
# things like "треба розуміти, як працює <head> сторінки" — an opening-tag test
# calls that markup and hands it to a parser that deletes the very phrase the
# sentence is about. Nobody writes `</p>` in prose.
_MARKUP_MARKERS = ("</p>", "</div>", "</ul>", "</ol>", "</li>", "</h", "<br")


def parse_stored_text(raw_text: str) -> ParsedDocument:
    """Parse a stored revision without knowing which adapter produced it.

    The Phase 1 backfill copied `job_source_records.description`, which is
    already flattened to text, while everything ingested since stores the
    source's markup. Both live in the same column, so the form is sniffed rather
    than assumed: parsing markup as plain text would leave the tags in the
    evidence spans, and it is the spans that have to quote the document exactly.

    Wrong in the safe direction. Markup misread as text is visible immediately —
    the tags are right there in the parsed output — while text misread as markup
    silently loses whatever sat between the angle brackets.
    """
    lowered = raw_text.lower()
    if any(marker in lowered for marker in _MARKUP_MARKERS):
        return parse_html(raw_text)
    return parse_plain_text(raw_text)


async def _pending(session: Any, limit: int) -> list[tuple[uuid.UUID, str, str, uuid.UUID | None]]:
    """Revisions with no parsed text, oldest first, excluding ones already tried.

    A document that cannot be parsed cannot be parsed on the second attempt
    either. Without the `failed` exclusion it would come back in every batch,
    and the backlog would never reach zero — the task would report work left to
    do forever, over the same handful of rows.
    """
    result = await session.execute(
        select(
            DocumentRevisionModel.id,
            DocumentRevisionModel.entity_kind,
            DocumentRevisionModel.raw_text,
            DocumentRevisionModel.job_source_record_id,
        )
        .where(
            DocumentRevisionModel.parsed_text.is_(None),
            DocumentRevisionModel.status != RevisionStatus.FAILED.value,
        )
        .order_by(DocumentRevisionModel.created_at)
        .limit(limit)
    )
    return [(row[0], row[1], row[2], row[3]) for row in result.all()]


async def _normalized_fields(session: Any, source_record_id: uuid.UUID) -> dict[str, Any]:
    """The adapter's parsed fields for a revision that was stored without them.

    Read from the source record, which is the one place they exist for these
    rows. Ingestion deliberately snapshots them onto the revision instead,
    because `job_source_records` is upserted on every re-scrape and a later
    reader would otherwise pair old text with today's fields. That risk does not
    apply here: these are all revision 1 of a document with no other revision, so
    the record and the text are still the same version of the vacancy. Writing
    the snapshot now is what keeps it that way if the vacancy changes later.
    """
    record = await session.get(JobSourceRecordModel, source_record_id)
    if record is None:
        return {}
    return {
        "title": record.title,
        "seniority": record.seniority,
        "employment_type": record.employment_type,
        "remote": record.remote,
        "required_experience_years": record.required_experience_years,
        "salary_min": record.salary_min,
        "salary_max": record.salary_max,
        "salary_currency": record.salary_currency,
    }


async def _parse_one(
    session: Any,
    revision_id: uuid.UUID,
    entity_kind: str,
    raw_text: str,
    source_record_id: uuid.UUID | None,
) -> None:
    documents = DocumentRepository(session)
    parsed = parse_stored_text(raw_text)
    language = detect_language(parsed.text)

    if source_record_id is not None:
        await documents.set_normalized_fields(
            revision_id, await _normalized_fields(session, source_record_id)
        )

    await documents.store_parse(
        revision_id,
        parsed_text=parsed.text,
        blocks=[
            (block.ordinal, block.block_type, block.text, block.start_char, block.end_char)
            for block in parsed.blocks
        ],
        parser_name=parsed.parser_name,
        parser_version=parsed.parser_version,
        language_code=language,
    )
    # `searchable -> parsed` is a reprocess, an edge the state machine already
    # allows (models.ALLOWED_TRANSITIONS) precisely for this: the raw text has
    # not changed, only what we now do with it.
    await documents.transition(revision_id, RevisionStatus.PARSED, reason="phase 4 backfill")

    # Extraction runs off the outbox, not off the status — a revision left in
    # `parsed` with no event is a revision nothing will ever pick up. Appended in
    # the same transaction as the parse, which is the whole point of the outbox.
    OutboxRepository(session).append(
        aggregate_type=DOCUMENT_REVISION_AGGREGATE,
        aggregate_id=str(revision_id),
        event_type=DOCUMENT_REVISION_CREATED,
        payload={
            "entity_kind": entity_kind,
            "revision_no": 1,
            "language_code": language,
            "backfilled": True,
        },
    )


async def _run(limit: int) -> dict[str, Any]:
    async with session_scope() as session:
        pending = await _pending(session, limit)

    parsed = failed = 0
    for revision_id, entity_kind, raw_text, source_record_id in pending:
        # One transaction per revision. A batch that shares one would lose the
        # whole batch's work to a single unparseable document, and these are
        # documents nobody has parsed before — exactly where a surprise lives.
        try:
            async with session_scope() as session:
                await _parse_one(
                    session,
                    revision_id,
                    entity_kind,
                    raw_text,
                    source_record_id if entity_kind == EntityKind.JOB.value else None,
                )
            parsed += 1
        except Exception as exc:
            failed += 1
            logger.warning("backfill failed for revision %s", revision_id, exc_info=True)
            # Its own transaction: the one above rolled back, and the point of
            # recording the failure is that it survives.
            try:
                async with session_scope() as session:
                    await DocumentRepository(session).transition(
                        revision_id,
                        RevisionStatus.FAILED,
                        reason="phase 4 backfill",
                        failure_code=FAILURE_BACKFILL_PARSE,
                        failure_detail=str(exc)[:500],
                    )
            except Exception:
                logger.warning(
                    "could not record the backfill failure for %s", revision_id, exc_info=True
                )

    async with session_scope() as session:
        remaining = len(await _pending(session, 1))

    return {
        "status": "ok" if remaining else "done",
        "parsed": parsed,
        "failed": failed,
        "more_pending": bool(remaining),
    }


@celery_app.task(name="backfill.parse_revisions")
def parse_revisions(limit: int = BATCH) -> dict[str, Any]:
    """Parse a batch of pre-Phase-2 revisions so extraction and linking can run.

    Re-runnable, and safe to call when there is nothing to do. Call it until
    `status` comes back `done`.
    """
    return asyncio.run(_run(limit))


# --- re-extracting under a new model -----------------------------------------

# Revisions already `extracted`, whose newest profile was not produced by the
# extractor named here. DISTINCT ON gives the newest profile per revision, which
# is the only one that decides whether the revision is up to date — an older one
# from a previous model is history, not a reason to skip.
_STALE_PROFILES = """
    SELECT r.id, r.entity_kind, p.extractor_model_id
    FROM document_revisions r
    JOIN (
        SELECT DISTINCT ON (document_revision_id)
               document_revision_id, extractor_model_id, created_at
        FROM profile_revisions
        ORDER BY document_revision_id, created_at DESC
    ) p ON p.document_revision_id = r.id
    WHERE r.status = 'extracted'
      AND r.parsed_text IS NOT NULL
      AND p.extractor_model_id IS DISTINCT FROM :fingerprint
    ORDER BY r.created_at
    LIMIT :limit
"""


async def _current_fingerprint() -> str:
    """What the running ml-service would stamp on a profile it produced.

    Asked of the container rather than read from settings, for the same reason
    /info exists: the configuration says what should be running, and only the
    process knows what is.
    """
    url = get_settings().ml_service_url
    if not url:
        raise RuntimeError("ML_SERVICE_URL is not set; there is no model to re-extract with")
    info = await MlServiceClient(url).info()
    if not info.get("loaded"):
        raise RuntimeError("ml-service is up but has not finished loading its weights")
    return f"{info['extractor_model_id']}@{info['extractor_revision'][:12]}"


async def _run_reextract(limit: int) -> dict[str, Any]:
    fingerprint = await _current_fingerprint()

    async with session_scope() as session:
        rows = (
            await session.execute(
                text(_STALE_PROFILES), {"fingerprint": fingerprint, "limit": limit}
            )
        ).all()

    queued = failed = 0
    for revision_id, entity_kind, _previous in rows:
        try:
            async with session_scope() as session:
                documents = DocumentRepository(session)
                # `extracted -> parsed` is the reprocess edge (models.py): the
                # raw text has not changed, so this rewinds rather than making a
                # new revision. The profile it had is left in place until the new
                # one is written, so nothing is ever without a profile.
                await documents.transition(
                    revision_id, RevisionStatus.PARSED, reason=f"re-extract for {fingerprint}"
                )
                OutboxRepository(session).append(
                    aggregate_type=DOCUMENT_REVISION_AGGREGATE,
                    aggregate_id=str(revision_id),
                    event_type=DOCUMENT_REVISION_CREATED,
                    payload={"entity_kind": entity_kind, "reextraction": True},
                )
            queued += 1
        except Exception:
            failed += 1
            logger.warning(
                "could not queue revision %s for re-extraction", revision_id, exc_info=True
            )

    async with session_scope() as session:
        remaining = len(
            (
                await session.execute(
                    text(_STALE_PROFILES), {"fingerprint": fingerprint, "limit": 1}
                )
            ).all()
        )

    return {
        "status": "ok" if remaining else "done",
        "model": fingerprint,
        "queued": queued,
        "failed": failed,
        "more_pending": bool(remaining),
    }


@celery_app.task(name="backfill.reextract_revisions")
def reextract_revisions(limit: int = BATCH) -> dict[str, Any]:
    """Queue documents whose profile predates the current model.

    Re-runnable and self-limiting: what it selects is "the newest profile was
    not made by the extractor that is running now", so a finished corpus selects
    nothing and a model upgrade selects everything again without a flag to set.

    It only queues. The outbox relay does the extraction, at its own rate, which
    is what keeps a few thousand re-extractions from monopolising the worker.
    """
    return asyncio.run(_run_reextract(limit))


# --- re-linking after the taxonomy changes -----------------------------------

# The newest profile per document revision, with the text its spans index into.
# Only the newest matters: older ones are history, and re-linking them would
# write mentions for a profile nothing reads.
_NEWEST_PROFILES = """
    SELECT p.id, p.extracted_profile, r.parsed_text
    FROM (
        SELECT DISTINCT ON (document_revision_id) id, document_revision_id,
               extracted_profile, created_at
        FROM profile_revisions
        ORDER BY document_revision_id, created_at DESC
    ) p
    JOIN document_revisions r ON r.id = p.document_revision_id
    WHERE r.parsed_text IS NOT NULL
    ORDER BY p.id
    OFFSET :offset LIMIT :limit
"""


def _spans_of(profile: dict[str, Any]) -> list[ExtractedSpan]:
    """The competencies a stored profile holds, as linker input.

    Read back out of the profile rather than re-extracted: the model already
    decided which phrases are competencies, and that decision does not change
    because the taxonomy did. This is the whole reason re-linking is cheap
    enough to run over the corpus — no forward pass, just a dictionary lookup
    per mention.
    """
    spans: list[ExtractedSpan] = []
    for competency in profile.get("competencies") or []:
        evidence = competency.get("evidence")
        if not evidence:
            continue
        spans.append(
            ExtractedSpan(
                raw_text=competency["raw_text"],
                start_char=evidence["start_char"],
                end_char=evidence["end_char"],
                confidence=float(competency.get("confidence", 1.0)),
            )
        )
    return spans


async def _relink_batch(offset: int, limit: int) -> tuple[int, int, int]:
    async with session_scope() as session:
        rows = (
            await session.execute(text(_NEWEST_PROFILES), {"offset": offset, "limit": limit})
        ).all()
        if not rows:
            return 0, 0, 0

        linker = ConceptLinkingService(TaxonomyRepository(session), MentionRepository(session))
        linked = relinked = 0
        for profile_id, profile, parsed_text in rows:
            spans = _spans_of(profile or {})
            result = await linker.link(profile_id, parsed_text, spans=spans or None)
            linked += result.linked
            relinked += 1
        return len(rows), relinked, linked


async def _run_relink(batch: int) -> dict[str, Any]:
    offset = profiles = linked = 0
    while True:
        seen, relinked, batch_linked = await _relink_batch(offset, batch)
        if not seen:
            break
        profiles += relinked
        linked += batch_linked
        offset += seen
    return {"status": "done", "profiles": profiles, "linked": linked}


@celery_app.task(name="backfill.relink_profiles")
def relink_profiles(batch: int = 200) -> dict[str, Any]:
    """Re-link every current profile against the taxonomy as it stands now.

    Run after promoting terms into internal concepts (spec 9.4): a promotion
    changes what the linker can find, but stored mentions were written by an
    earlier lookup and do not move on their own.

    Deliberately not a re-extraction. Nothing about the document or the model
    changed — only the taxonomy — so paying five seconds a document for a
    forward pass would be five seconds spent reproducing the answer we already
    have. `replace_for_profile` makes it idempotent, so running it twice costs
    time and changes nothing.
    """
    return asyncio.run(_run_relink(batch))


# --- promotions made before promotion did anything ---------------------------


async def _run_promote_legacy() -> dict[str, Any]:
    async with session_scope() as session:
        pending = (
            (
                await session.execute(
                    text(
                        "SELECT normalized_text FROM unmapped_mentions "
                        "WHERE status = 'promoted' AND promoted_concept_id IS NULL "
                        "ORDER BY occurrences DESC"
                    )
                )
            )
            .scalars()
            .all()
        )

    if not pending:
        return {"status": "done", "promoted": 0, "created": 0}

    async with session_scope() as session:
        service = ConceptPromotionService(TaxonomyRepository(session), MentionRepository(session))
        outcomes = await service.review_many([(term, "promoted") for term in pending])

    return {
        "status": "done",
        "promoted": len(outcomes),
        "created": sum(1 for outcome in outcomes if outcome.created),
    }


@celery_app.task(name="backfill.promote_legacy_reviews")
def promote_legacy_reviews() -> dict[str, Any]:
    """Create concepts for terms marked `promoted` before promotion did anything.

    For the window between the review screen shipping and 9.4's second half
    landing, "Worth adding" wrote a status and nothing else. Those decisions are
    real and were made by a person; this is what carries them out.

    Re-runnable: it selects only promotions with no concept, so a finished run
    selects nothing. Reversible one term at a time from the review screen, which
    now retires the concept as well as clearing the mark.
    """
    return asyncio.run(_run_promote_legacy())
