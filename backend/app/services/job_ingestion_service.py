"""Orchestrates the Raw -> Normalized -> Canonical pipeline for one source: discover,
fetch details for genuinely new jobs only, normalize, dedup, persist.

Already-known jobs are skipped before any detail fetch — see docs/source-adapters.md
("detail HTML is only fetched for jobs not already seen"). Re-running this for a
source is always safe: raw_jobs and job_source_records are upserted on
(source, external_id), so nothing duplicates.

One bad listing (a detail page that fails to fetch, or normalizes into something
the adapter can't parse) must not sink the rest of the batch — see ingest_source,
which isolates each listing's failure the same way scrape.fetch_source already
isolates one source's failure from the rest of the platform.
"""

import logging
import uuid
from dataclasses import dataclass

from app.domain.documents.language import detect_language
from app.domain.documents.models import EntityKind, RevisionStatus, compute_content_hash
from app.domain.documents.parsing import ParsedDocument, parse_plain_text
from app.domain.jobs.deduplication import DeduplicationService
from app.domain.jobs.models import NormalizedJob, RawJob
from app.integrations.parsers.html import parse_html
from app.integrations.sources.base import JobSearchCriteria, JobSourceAdapter
from app.repositories.document_repository import DocumentRepository
from app.repositories.job_repository import JobRepository
from app.repositories.outbox_repository import OutboxRepository

logger = logging.getLogger(__name__)

# Emitted when a new version of a document's text is stored. Phase 3's extractor
# is the consumer; nothing handles it yet, and the relay says so rather than
# letting unhandled events pile up.
DOCUMENT_REVISION_CREATED = "document_revision_created"


def raw_document_text(normalized: NormalizedJob) -> str:
    """What a revision stores as its raw text, and what its hash is taken over.

    Prefers the source's markup when it survived normalization. `description` has
    already been flattened by `html_to_text`, which drops the blank lines that
    separated one section from the next — that flattened form collapses into a
    single paragraph when parsed, and Phase 3 would have no structure left to
    read necessity from. The markup still has the headings and the list items.
    """
    return normalized.description_html or normalized.description


def parse_description(normalized: NormalizedJob) -> tuple[str, ParsedDocument]:
    """The raw text, and the blocks parsed out of it."""
    raw_text = raw_document_text(normalized)
    if normalized.description_html:
        return raw_text, parse_html(raw_text)
    return raw_text, parse_plain_text(raw_text)


@dataclass(frozen=True)
class IngestionResult:
    jobs_seen: int
    jobs_processed: int
    processed_canonical_job_ids: list[str]


class JobIngestionService:
    def __init__(
        self,
        job_repository: JobRepository,
        dedup_service: DeduplicationService | None = None,
        document_repository: DocumentRepository | None = None,
        outbox: OutboxRepository | None = None,
    ):
        self._job_repository = job_repository
        self._dedup_service = dedup_service or DeduplicationService()
        self._document_repository = document_repository
        self._outbox = outbox

    async def ingest_source(
        self,
        adapter: JobSourceAdapter,
        search: JobSearchCriteria,
        max_jobs: int | None = None,
    ) -> IngestionResult:
        """max_jobs caps how many discovered listings this call will even attempt to
        detail-fetch — a safety ceiling on run cost, not a guarantee of exactly N
        newly-processed jobs (already-known listings still get skipped for free
        within that cap, same as without one)."""
        discovery = await adapter.fetch_jobs(search)
        listings = discovery.raw_jobs[:max_jobs] if max_jobs is not None else discovery.raw_jobs

        canonical_job_ids: list[str] = []
        for listing in listings:
            if await self._job_repository.raw_job_exists(listing.source, listing.external_id):
                continue

            try:
                detail_raw_job = await adapter.fetch_job_details(listing.external_id, listing.url)
                canonical_job_id = await self._ingest_one(adapter, detail_raw_job)
            except Exception:
                logger.warning(
                    "failed to ingest listing %s/%s — skipping it, continuing with the rest "
                    "of this batch",
                    listing.source,
                    listing.external_id,
                    exc_info=True,
                )
                continue
            canonical_job_ids.append(str(canonical_job_id))

        return IngestionResult(
            jobs_seen=len(discovery.raw_jobs),
            jobs_processed=len(canonical_job_ids),
            processed_canonical_job_ids=canonical_job_ids,
        )

    async def ingest_raw_job(self, adapter: JobSourceAdapter, raw_job: RawJob) -> uuid.UUID:
        """Normalize + dedup a single already-fetched RawJob. Used by the `normalize`
        worker task when raw storage and detail-fetching already happened separately."""
        raw_job_id = await self._job_repository.upsert_raw_job(raw_job)
        return await self._ingest_one(adapter, raw_job, raw_job_id=raw_job_id)

    async def _ingest_one(
        self,
        adapter: JobSourceAdapter,
        raw_job: RawJob,
        raw_job_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        if raw_job_id is None:
            raw_job_id = await self._job_repository.upsert_raw_job(raw_job)

        normalized: NormalizedJob = adapter.normalize(raw_job)
        canonical_job_id = await self._dedup(normalized)
        source_record_id = await self._job_repository.save_normalized_job(
            raw_job_id, normalized, canonical_job_id
        )
        await self._record_revision(source_record_id, normalized)
        return canonical_job_id

    async def _record_revision(
        self, source_record_id: uuid.UUID, normalized: NormalizedJob
    ) -> None:
        """Store this version of the vacancy's text, if it is a version we do not
        already have.

        Re-scraping an unchanged vacancy is the common case and must cost
        nothing: `record` compares the content hash and returns `created=False`,
        and parsing is not repeated for a revision that already exists.

        Not defended with a try/except on purpose. `ingest_source` already
        isolates one listing's failure from the rest of the batch, so a bug here
        skips that vacancy and says so in the log — where swallowing the error
        would leave the corpus quietly missing revisions until Phase 3 went
        looking for them.
        """
        if self._document_repository is None:
            return

        # Hash first, parse second. The hash needs only the raw text, so an
        # unchanged vacancy is recognised without paying for a parse it would
        # throw away — and language detection needs the *parsed* text, since the
        # Latin tag names in raw markup would outvote a short Ukrainian body.
        raw_text = raw_document_text(normalized)
        revision, created = await self._document_repository.record(
            EntityKind.JOB,
            source_record_id,
            content_hash=compute_content_hash(raw_text),
            raw_text=raw_text,
        )
        if not created:
            return

        _, parsed = parse_description(normalized)
        await self._document_repository.store_parse(
            uuid.UUID(revision.id),
            parsed_text=parsed.text,
            blocks=[
                (block.ordinal, block.block_type, block.text, block.start_char, block.end_char)
                for block in parsed.blocks
            ],
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
            language_code=detect_language(parsed.text),
        )
        await self._document_repository.transition(
            uuid.UUID(revision.id), RevisionStatus.PARSED, reason="parsed on ingest"
        )
        if self._outbox is not None:
            # Same transaction as the revision itself. An event committed
            # separately could be lost while the revision survived, which is the
            # gap the outbox exists to close.
            self._outbox.append(
                aggregate_type="document_revision",
                aggregate_id=revision.id,
                event_type=DOCUMENT_REVISION_CREATED,
                payload={
                    "entity_kind": EntityKind.JOB.value,
                    "revision_no": revision.revision_no,
                    "language_code": detect_language(parsed.text),
                },
            )
        for warning in parsed.warnings:
            logger.warning("revision %s: %s", revision.id, warning)

    async def _dedup(self, normalized: NormalizedJob) -> uuid.UUID:
        candidates = await self._job_repository.list_canonical_jobs()
        match = self._dedup_service.find_canonical_match(normalized, candidates)

        if match is not None:
            canonical_job_id = uuid.UUID(match.id)
            await self._job_repository.touch_canonical_job(canonical_job_id)
            return canonical_job_id

        return await self._job_repository.create_canonical_job(normalized)
