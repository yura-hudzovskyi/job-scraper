"""Ingestion writing document revisions.

The behaviour under test is mostly about *not* doing work: a scrape re-reads the
same vacancy on every run, and the second read must store nothing and parse
nothing. The rest is that the markup survives far enough to be parsed, because
the flattened `description` has had the structure Phase 3 needs stripped out of
it already.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.documents.models import (
    BlockType,
    EntityKind,
    RevisionStatus,
    compute_content_hash,
)
from app.domain.jobs.models import (
    EmploymentType,
    JobLocation,
    NormalizedJob,
    RawJob,
)
from app.services.cv_service import CvService, mime_type_of
from app.services.job_ingestion_service import JobIngestionService, parse_description

DESCRIPTION_HTML = (
    "<h2>Вимоги</h2><ul><li>Python</li><li>PostgreSQL</li></ul>"
    "<h2>Буде плюсом</h2><ul><li>Kubernetes</li></ul>"
)
# What html_to_text produces: one line per block, blank lines gone.
FLATTENED = "Вимоги\nPython\nPostgreSQL\nБуде плюсом\nKubernetes"


def _job(description: str = FLATTENED, html: str | None = DESCRIPTION_HTML) -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="123",
        url="https://example.com/123",
        title="Backend Engineer",
        company="Example",
        description=description,
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=True),
        description_html=html,
    )


# --- which text gets parsed --------------------------------------------------


def test_the_source_markup_is_parsed_when_it_survived_normalization() -> None:
    raw_text, parsed = parse_description(_job())

    assert raw_text == DESCRIPTION_HTML
    kinds = [block.block_type for block in parsed.blocks]
    assert BlockType.HEADING in kinds
    assert BlockType.LIST_ITEM in kinds


def test_parsing_the_flattened_text_would_have_lost_that_structure() -> None:
    """html_to_text drops the blank lines between sections, so plain-text parsing
    merges the whole vacancy into one paragraph. This is the reason
    description_html is carried through normalization at all."""
    _, parsed = parse_description(_job(html=None))

    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].block_type is BlockType.PARAGRAPH


def test_a_source_without_markup_still_parses() -> None:
    raw_text, parsed = parse_description(_job(description="Plain text vacancy", html=None))

    assert raw_text == "Plain text vacancy"
    assert parsed.blocks


def test_the_parsed_spans_resolve_either_way() -> None:
    for job in (_job(), _job(html=None)):
        _, parsed = parse_description(job)
        assert parsed.spans_resolve()


# --- the content hash --------------------------------------------------------


def test_the_hash_is_taken_over_the_raw_text_not_the_parsed_text() -> None:
    """parsed_text is a function of (raw_text, parser_version), both stored
    separately. Hashing the raw text means improving the parser re-parses
    existing revisions instead of manufacturing a new one for every document."""
    raw_text, parsed = parse_description(_job())

    assert compute_content_hash(raw_text) != compute_content_hash(parsed.text)


def test_the_hash_matches_the_backfill_migrations_definition() -> None:
    """The Phase 1 migration used encode(sha256(convert_to(text,'UTF8')),'hex').
    If these disagree, every backfilled document looks changed on its next
    scrape and the corpus doubles."""
    import hashlib

    text = "Вимоги до кандидата"
    assert compute_content_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert len(compute_content_hash(text)) == 64


# --- idempotency -------------------------------------------------------------


class _FakeDocumentRepository:
    """Records what it was asked to do, and answers `record` the way the real one
    does: the same content hash twice means the second call created nothing."""

    def __init__(self) -> None:
        self.hashes: dict[tuple[EntityKind, uuid.UUID], str] = {}
        self.records: list[tuple[EntityKind, uuid.UUID, str]] = []
        self.mime_types: list[str | None] = []
        self.parses: list[uuid.UUID] = []
        self.languages: list[str | None] = []
        self.transitions: list[tuple[uuid.UUID, RevisionStatus]] = []

    async def record(
        self,
        entity_kind: EntityKind,
        owner_id: uuid.UUID,
        content_hash: str,
        raw_text: str,
        mime_type: str | None = None,
        language_code: str | None = None,
        raw_payload: dict[str, object] | None = None,
    ) -> tuple[object, bool]:
        self.records.append((entity_kind, owner_id, content_hash))
        self.mime_types.append(mime_type)
        key = (entity_kind, owner_id)
        created = self.hashes.get(key) != content_hash
        self.hashes[key] = content_hash

        class _Revision:
            id = str(uuid.uuid4())

        return _Revision(), created

    async def store_parse(
        self,
        revision_id: uuid.UUID,
        parsed_text: str,
        blocks: list[tuple[int, BlockType, str, int, int]],
        parser_name: str,
        parser_version: str,
        language_code: str | None = None,
    ) -> int:
        self.parses.append(revision_id)
        self.languages.append(language_code)
        return len(blocks)

    async def transition(
        self,
        revision_id: uuid.UUID,
        target: RevisionStatus,
        reason: str | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
    ) -> object:
        self.transitions.append((revision_id, target))
        return object()

    async def delete_for_owners(
        self, entity_kind: EntityKind, owner_ids: list[uuid.UUID]
    ) -> dict[str, int]:
        return {}


class _FakeCandidateRepository:
    def __init__(self) -> None:
        self.saved: list[str] = []

    async def save_cv_document(self, user_id: uuid.UUID, filename: str, raw_text: str) -> object:
        from app.domain.candidates.models import CvDocument

        self.saved.append(raw_text)
        return CvDocument(
            id=str(uuid.uuid4()),
            user_id=str(user_id),
            filename=filename,
            raw_text=raw_text,
            uploaded_at=None,  # type: ignore[arg-type]
        )


class _FakeJobRepository:
    def __init__(self) -> None:
        self.source_record_id = uuid.uuid4()
        self.canonical_job_id = uuid.uuid4()

    async def upsert_raw_job(self, raw_job: object) -> uuid.UUID:
        return uuid.uuid4()

    async def list_canonical_jobs(self) -> list[object]:
        return []

    async def create_canonical_job(self, normalized: NormalizedJob) -> uuid.UUID:
        return self.canonical_job_id

    async def touch_canonical_job(self, canonical_job_id: uuid.UUID) -> None:
        return None

    async def save_normalized_job(
        self, raw_job_id: uuid.UUID, normalized: NormalizedJob, canonical_job_id: uuid.UUID
    ) -> uuid.UUID:
        return self.source_record_id


class _FakeAdapter:
    source_name = "dou"

    def __init__(self, normalized: NormalizedJob):
        self._normalized = normalized

    def normalize(self, raw_job: object) -> NormalizedJob:
        return self._normalized


def _raw_job() -> RawJob:
    return RawJob(
        source="dou",
        external_id="123",
        url="https://example.com/123",
        payload={},
        fetched_at=datetime(2026, 9, 4, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_ingesting_a_new_vacancy_records_and_parses_one_revision() -> None:
    documents = _FakeDocumentRepository()
    service = JobIngestionService(
        _FakeJobRepository(),  # type: ignore[arg-type]
        document_repository=documents,  # type: ignore[arg-type]
    )

    await service.ingest_raw_job(_FakeAdapter(_job()), _raw_job())  # type: ignore[arg-type]

    assert len(documents.records) == 1
    assert documents.records[0][0] is EntityKind.JOB
    assert len(documents.parses) == 1
    assert documents.transitions == [(documents.parses[0], RevisionStatus.PARSED)]


@pytest.mark.asyncio
async def test_re_scraping_an_unchanged_vacancy_stores_and_parses_nothing() -> None:
    """The common case by a wide margin — every scrape re-reads vacancies it has
    already seen. A second revision here would double the corpus weekly."""
    documents = _FakeDocumentRepository()
    jobs = _FakeJobRepository()
    service = JobIngestionService(jobs, document_repository=documents)  # type: ignore[arg-type]
    adapter = _FakeAdapter(_job())

    await service.ingest_raw_job(adapter, _raw_job())  # type: ignore[arg-type]
    await service.ingest_raw_job(adapter, _raw_job())  # type: ignore[arg-type]

    assert len(documents.records) == 2, "both ingests ask"
    assert len(documents.parses) == 1, "only the first one parses"
    assert len(documents.transitions) == 1


@pytest.mark.asyncio
async def test_a_changed_vacancy_is_parsed_again() -> None:
    documents = _FakeDocumentRepository()
    jobs = _FakeJobRepository()
    service = JobIngestionService(jobs, document_repository=documents)  # type: ignore[arg-type]

    await service.ingest_raw_job(_FakeAdapter(_job()), _raw_job())  # type: ignore[arg-type]
    changed = _job(html=DESCRIPTION_HTML + "<p>Now with a bonus scheme.</p>")
    await service.ingest_raw_job(_FakeAdapter(changed), _raw_job())  # type: ignore[arg-type]

    assert len(documents.parses) == 2
    assert documents.records[0][2] != documents.records[1][2]


@pytest.mark.asyncio
async def test_ingestion_without_a_document_repository_is_unchanged() -> None:
    """Phase 1 and 2 must both be safe to deploy independently of the migration."""
    service = JobIngestionService(_FakeJobRepository())  # type: ignore[arg-type]

    canonical_job_id = await service.ingest_raw_job(
        _FakeAdapter(_job()),  # type: ignore[arg-type]
        _raw_job(),
    )

    assert canonical_job_id is not None


@pytest.mark.asyncio
async def test_uploading_a_cv_records_a_parsed_revision() -> None:
    documents = _FakeDocumentRepository()
    service = CvService(_FakeCandidateRepository(), documents)  # type: ignore[arg-type]

    await service.upload_cv(uuid.uuid4(), "cv.txt", b"Experience\n\n- Python\n- Postgres")

    assert len(documents.records) == 1
    assert documents.records[0][0] is EntityKind.CANDIDATE
    assert documents.parses, "a new revision must have its blocks stored"
    assert documents.transitions == [(documents.parses[0], RevisionStatus.PARSED)]


@pytest.mark.asyncio
async def test_the_cv_revision_carries_the_detected_language_and_media_type() -> None:
    documents = _FakeDocumentRepository()
    candidates = _FakeCandidateRepository()
    service = CvService(candidates, documents)  # type: ignore[arg-type]
    ukrainian = "Досвід роботи інженером з розробки програмного забезпечення в команді"

    await service.upload_cv(uuid.uuid4(), "cv.txt", ukrainian.encode("utf-8"))

    assert documents.records[0][2] == compute_content_hash(ukrainian)
    assert documents.languages == ["uk"]
    assert documents.mime_types == ["text/plain"]


def test_the_media_type_is_a_media_type_not_an_extension() -> None:
    """`mime_type` is named for what it holds. "pdf" in that column is the same
    mistake as a filename in it, only less obvious to the next reader."""
    assert mime_type_of("cv.pdf") == "application/pdf"
    assert mime_type_of("CV.TXT") == "text/plain"
    assert mime_type_of("cv.rtf") is None


@pytest.mark.asyncio
async def test_the_language_is_detected_from_the_parsed_text_not_the_markup() -> None:
    """Raw markup carries Latin tag names. Detecting on it would outvote the body
    of a short Ukrainian vacancy and label it English."""
    documents = _FakeDocumentRepository()
    service = JobIngestionService(
        _FakeJobRepository(),  # type: ignore[arg-type]
        document_repository=documents,  # type: ignore[arg-type]
    )
    body = "Ми шукаємо інженера з досвідом роботи у розподілених системах команди"

    await service.ingest_raw_job(  # type: ignore[arg-type]
        _FakeAdapter(_job(html=f"<div><p>{body}</p></div>")),
        _raw_job(),
    )

    assert documents.languages == ["uk"]


@pytest.mark.asyncio
async def test_a_cv_upload_without_a_document_repository_still_works() -> None:
    """The argument is optional so a deployment that has not run the Phase 1
    migration keeps working."""
    service = CvService(_FakeCandidateRepository())  # type: ignore[arg-type]

    document = await service.upload_cv(uuid.uuid4(), "cv.txt", b"Some experience here")

    assert document.raw_text == "Some experience here"
