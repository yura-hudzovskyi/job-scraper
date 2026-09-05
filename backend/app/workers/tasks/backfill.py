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
"""

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

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
from app.integrations.parsers.html import parse_html
from app.repositories.document_repository import DocumentRepository
from app.repositories.outbox_repository import OutboxRepository
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
