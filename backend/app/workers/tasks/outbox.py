"""The relay that moves outbox events onto the queue, and purges delivered ones.

The guarantee it provides is about delivery: an event written in a committed
transaction is picked up, handed to whatever is registered, and marked published
exactly once per success, with failures counted on their own row rather than
blocking the events behind them.

An event with no handler is still delivered — marked published and counted as
unhandled. Leaving it pending would build a backlog of events nobody is ever
going to want, and hide a genuinely stuck one among them.

Extraction runs from here rather than inline with ingestion because it calls a
model — about two seconds per vacancy, measured: off the scrape path, a slow or
failing extractor degrades throughput instead of taking scraping down with it,
and a retry costs nothing because the revision's state machine already records
where it got to.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config.settings import get_settings
from app.db.session import session_scope
from app.domain.documents.events import DOCUMENT_REVISION_CREATED
from app.domain.profiles.extraction import ProfileExtractor
from app.domain.profiles.neural import NeuralExtractor
from app.domain.profiles.structural import StructuralExtractor
from app.integrations.ml_service import MlServiceClient
from app.repositories.document_repository import DocumentRepository
from app.repositories.mention_repository import MentionRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.profile_repository import ProfileRepository
from app.repositories.taxonomy_repository import TaxonomyRepository
from app.services.concept_linking_service import ConceptLinkingService
from app.services.extraction_service import ExtractionService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


def build_extractor() -> ProfileExtractor:
    """The neural extractor when `ml-service` is configured, structural otherwise.

    Reading the setting here rather than at import time is what lets the same
    image run in local development with no model container: nothing fails, the
    profiles are thinner, and each one records which extractor produced it.
    """
    url = get_settings().ml_service_url
    if not url:
        return StructuralExtractor()
    return NeuralExtractor(MlServiceClient(url))


async def extract_document_revision(aggregate_id: str, payload: dict[str, Any]) -> None:
    """Extract a profile from a newly parsed revision.

    Its own session, separate from the relay's: extraction is the unit of work
    that either lands or does not, and sharing the relay's transaction would let
    one revision's failure roll back the publication bookkeeping for every event
    in the batch.
    """
    async with session_scope() as session:
        service = ExtractionService(
            DocumentRepository(session),
            ProfileRepository(session),
            build_extractor(),
            ConceptLinkingService(TaxonomyRepository(session), MentionRepository(session)),
        )
        outcome = await service.extract(uuid.UUID(aggregate_id))
    if outcome.skipped_reason:
        logger.info("revision %s not extracted: %s", aggregate_id, outcome.skipped_reason)


# event_type -> handler. Which extractor runs is decided in `build_extractor`,
# inside the handler — this wiring did not change when GLiNER2 arrived.
HANDLERS: dict[str, EventHandler] = {
    DOCUMENT_REVISION_CREATED: extract_document_revision,
}

# How long a delivered event is kept. Long enough to answer "did that ingest
# actually emit an event" during an incident, short enough that the table does
# not grow forever.
PUBLISHED_RETENTION_DAYS = 7

# Sized so one relay run finishes inside beat's sixty-second tick rather than
# piling up behind itself. Extraction now costs about 5.5 s per document
# (17.6), so a hundred of them is nine minutes of work started every minute.
# It does not change throughput — ml-service serialises on one model either way
# — only how long a transaction holds its claimed rows.
BATCH_SIZE = 10


async def _relay() -> dict[str, int]:
    published = failed = unhandled = 0

    async with session_scope() as session:
        outbox = OutboxRepository(session)
        events = await outbox.unpublished(BATCH_SIZE)
        delivered: list[int] = []

        for event in events:
            handler = HANDLERS.get(event.event_type)
            if handler is None:
                unhandled += 1
                delivered.append(event.id)
                continue
            try:
                await handler(event.aggregate_id, event.payload)
            except Exception as exc:
                # One event that will not publish must not block the ones behind
                # it: the failure is counted on its own row and the relay moves on.
                failed += 1
                logger.warning(
                    "outbox event %d (%s) failed to publish",
                    event.id,
                    event.event_type,
                    exc_info=True,
                )
                await outbox.record_failure(event.id, str(exc))
                continue
            delivered.append(event.id)
            published += 1

        await outbox.mark_published(delivered)
        pending = await outbox.count_pending()

    return {
        "published": published,
        "unhandled": unhandled,
        "failed": failed,
        "pending": pending,
    }


async def _purge() -> int:
    async with session_scope() as session:
        cutoff = datetime.now(UTC) - timedelta(days=PUBLISHED_RETENTION_DAYS)
        return await OutboxRepository(session).purge_published(cutoff)


@celery_app.task(name="outbox.relay")
def relay() -> dict[str, int]:
    return asyncio.run(_relay())


@celery_app.task(name="outbox.purge_published")
def purge_published() -> dict[str, int]:
    return {"purged": asyncio.run(_purge())}
