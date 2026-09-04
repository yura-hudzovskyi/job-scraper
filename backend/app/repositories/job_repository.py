"""Persistence for the Raw -> Normalized -> Canonical job pipeline (docs/domain-model.md).

unique(source, external_id) on raw_jobs and job_source_records makes re-scraping
and re-normalizing idempotent — upserts, never duplicates.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job import (
    CanonicalJobModel,
    JobSourceRecordModel,
    RawJobModel,
    ScrapeRunModel,
)
from app.domain.jobs.models import (
    CanonicalJob,
    EmploymentType,
    JobLocation,
    NormalizedJob,
    RawJob,
    SalaryRange,
)
from app.domain.jobs.scrape_rotation import pick_next_category
from app.repositories.base import rows_affected


def _to_raw_job(model: RawJobModel) -> RawJob:
    return RawJob(
        source=model.source,
        external_id=model.external_id,
        url=model.url,
        payload=model.payload,
        fetched_at=model.fetched_at,
    )


def _canonical_candidate_view(model: CanonicalJobModel) -> NormalizedJob:
    """A minimal NormalizedJob carrying only the fields DeduplicationService reads
    (company/title/description) — canonical_jobs doesn't store a full normalized
    snapshot, since the real one lives on its JobSourceRecords."""
    return NormalizedJob(
        source="canonical",
        external_id=str(model.id),
        url="",
        title=model.title,
        company=model.company,
        description=model.description,
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=False),
    )


def _to_normalized_job(model: JobSourceRecordModel) -> NormalizedJob:
    salary = (
        SalaryRange(min=model.salary_min, max=model.salary_max, currency=model.salary_currency)
        if model.salary_min is not None or model.salary_max is not None
        else None
    )
    return NormalizedJob(
        source=model.source,
        external_id=model.external_id,
        url=model.url,
        title=model.title,
        company=model.company,
        description=model.description,
        employment_type=EmploymentType(model.employment_type),
        location=JobLocation(
            remote=model.remote, countries=list(model.countries), cities=list(model.cities)
        ),
        salary=salary,
        seniority=model.seniority,
        required_experience_years=model.required_experience_years,
    )


class JobRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    # --- raw ---

    async def count_raw_jobs_by_source(self) -> dict[str, int]:
        result = await self._session.execute(
            select(RawJobModel.source, func.count()).group_by(RawJobModel.source)
        )
        return {source: count for source, count in result.all()}

    async def raw_job_exists(self, source: str, external_id: str) -> bool:
        result = await self._session.execute(
            select(RawJobModel.id).where(
                RawJobModel.source == source, RawJobModel.external_id == external_id
            )
        )
        return result.first() is not None

    async def upsert_raw_job(self, raw_job: RawJob) -> uuid.UUID:
        """Insert or refresh payload/fetched_at for (source, external_id)."""
        stmt = (
            insert(RawJobModel)
            .values(
                source=raw_job.source,
                external_id=raw_job.external_id,
                url=raw_job.url,
                payload=raw_job.payload,
                fetched_at=raw_job.fetched_at,
            )
            .on_conflict_do_update(
                index_elements=[RawJobModel.source, RawJobModel.external_id],
                set_={
                    "url": raw_job.url,
                    "payload": raw_job.payload,
                    "fetched_at": raw_job.fetched_at,
                },
            )
            .returning(RawJobModel.id)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one()

    # --- scrape rotation ---

    async def get_least_recently_scraped_category(self, source: str, categories: list[str]) -> str:
        """Which of `categories` to scrape next for this source — whichever has
        gone longest without a run, or has never run at all. See
        scrape_rotation.py for the selection itself."""
        result = await self._session.execute(
            select(ScrapeRunModel.category, func.max(ScrapeRunModel.started_at))
            .where(ScrapeRunModel.source == source, ScrapeRunModel.category.in_(categories))
            .group_by(ScrapeRunModel.category)
        )
        last_scraped = {category: started_at for category, started_at in result.all() if category}
        return pick_next_category(categories, last_scraped)

    async def record_scrape_run(
        self,
        source: str,
        category: str,
        started_at: datetime,
        finished_at: datetime,
        jobs_seen: int,
        new_count: int,
        errors: int,
    ) -> None:
        self._session.add(
            ScrapeRunModel(
                source=source,
                category=category,
                started_at=started_at,
                finished_at=finished_at,
                jobs_seen=jobs_seen,
                new_count=new_count,
                errors=errors,
            )
        )
        await self._session.flush()

    async def list_recent_scrape_runs(self, limit: int = 10) -> list[ScrapeRunModel]:
        result = await self._session.execute(
            select(ScrapeRunModel).order_by(ScrapeRunModel.started_at.desc()).limit(limit)
        )
        return list(result.scalars())

    # --- canonical ---

    async def count_canonical_jobs(self, exclude_ids: set[uuid.UUID] | None = None) -> int:
        stmt = select(func.count()).select_from(CanonicalJobModel)
        if exclude_ids:
            stmt = stmt.where(CanonicalJobModel.id.notin_(exclude_ids))
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_all_canonical_job_ids(self) -> list[uuid.UUID]:
        result = await self._session.execute(select(CanonicalJobModel.id))
        return list(result.scalars())

    async def list_canonical_jobs(
        self,
        limit: int | None = None,
        offset: int = 0,
        exclude_ids: set[uuid.UUID] | None = None,
    ) -> list[CanonicalJob]:
        """Canonical jobs, newest-seen first. `limit=None` returns every row —
        used by DeduplicationService, which needs the full candidate set. The jobs
        list API must always pass a real limit."""
        stmt = select(CanonicalJobModel).order_by(CanonicalJobModel.last_seen_at.desc())
        if exclude_ids:
            stmt = stmt.where(CanonicalJobModel.id.notin_(exclude_ids))
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        canonical_models = result.scalars().all()
        if not canonical_models:
            return []

        source_records_result = await self._session.execute(
            select(JobSourceRecordModel.canonical_job_id, JobSourceRecordModel.id).where(
                JobSourceRecordModel.canonical_job_id.in_([m.id for m in canonical_models])
            )
        )
        source_record_ids: dict[uuid.UUID, list[str]] = {}
        for canonical_id, source_record_id in source_records_result.all():
            source_record_ids.setdefault(canonical_id, []).append(str(source_record_id))

        return [
            CanonicalJob(
                id=str(model.id),
                normalized=_canonical_candidate_view(model),
                source_records=source_record_ids.get(model.id, []),
            )
            for model in canonical_models
        ]

    async def get_canonical_job(self, canonical_job_id: uuid.UUID) -> CanonicalJob | None:
        model = await self._session.get(CanonicalJobModel, canonical_job_id)
        if model is None:
            return None
        result = await self._session.execute(
            select(JobSourceRecordModel.id).where(
                JobSourceRecordModel.canonical_job_id == canonical_job_id
            )
        )
        return CanonicalJob(
            id=str(model.id),
            normalized=_canonical_candidate_view(model),
            source_records=[str(row[0]) for row in result.all()],
        )

    async def get_normalized_job_for_canonical(
        self, canonical_job_id: uuid.UUID
    ) -> NormalizedJob | None:
        """The full NormalizedJob (salary, location, seniority, ...) for one of
        this canonical job's source records — canonical_jobs itself only stores
        the title/company/description subset used for dedup. Picks whichever
        source record was normalized most recently."""
        result = await self._session.execute(
            select(JobSourceRecordModel)
            .where(JobSourceRecordModel.canonical_job_id == canonical_job_id)
            .order_by(JobSourceRecordModel.normalized_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return _to_normalized_job(model) if model else None

    async def list_normalized_jobs_for_canonical(
        self, canonical_job_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, NormalizedJob]:
        """The same selection as above, for a whole batch in one query — what
        embedding and matching iterate over. Most recently normalized wins."""
        if not canonical_job_ids:
            return {}
        result = await self._session.execute(
            select(JobSourceRecordModel)
            .where(JobSourceRecordModel.canonical_job_id.in_(canonical_job_ids))
            .order_by(JobSourceRecordModel.normalized_at.desc())
        )
        jobs: dict[uuid.UUID, NormalizedJob] = {}
        for model in result.scalars():
            if model.canonical_job_id is not None:
                jobs.setdefault(model.canonical_job_id, _to_normalized_job(model))
        return jobs

    async def list_source_links_for_canonical(
        self, canonical_job_id: uuid.UUID
    ) -> list[tuple[str, str]]:
        """(source, url) pairs for every source this vacancy is known under — the
        Telegram card links out to each by name rather than one arbitrary URL."""
        result = await self._session.execute(
            select(JobSourceRecordModel.source, JobSourceRecordModel.url)
            .where(JobSourceRecordModel.canonical_job_id == canonical_job_id)
            .order_by(JobSourceRecordModel.normalized_at.desc())
        )
        links: dict[str, str] = {}
        for source, url in result.all():
            links.setdefault(source, url)
        return list(links.items())

    async def create_canonical_job(self, normalized: NormalizedJob) -> uuid.UUID:
        model = CanonicalJobModel(
            title=normalized.title,
            company=normalized.company,
            description=normalized.description,
        )
        self._session.add(model)
        await self._session.flush()
        return model.id

    async def touch_canonical_job(self, canonical_job_id: uuid.UUID) -> None:
        model = await self._session.get(CanonicalJobModel, canonical_job_id)
        if model is not None:
            model.last_seen_at = datetime.now(UTC)
            await self._session.flush()

    async def save_normalized_job(
        self,
        raw_job_id: uuid.UUID,
        normalized: NormalizedJob,
        canonical_job_id: uuid.UUID,
    ) -> uuid.UUID:
        """Upsert the JobSourceRecord for (source, external_id), attached to
        canonical_job_id."""
        common_fields = {
            "title": normalized.title,
            "company": normalized.company,
            "description": normalized.description,
            "employment_type": normalized.employment_type.value,
            "remote": normalized.location.remote,
            "countries": normalized.location.countries,
            "cities": normalized.location.cities,
            "salary_min": normalized.salary.min if normalized.salary else None,
            "salary_max": normalized.salary.max if normalized.salary else None,
            "salary_currency": normalized.salary.currency if normalized.salary else None,
            "seniority": normalized.seniority,
            "required_experience_years": normalized.required_experience_years,
        }
        stmt = (
            insert(JobSourceRecordModel)
            .values(
                raw_job_id=raw_job_id,
                canonical_job_id=canonical_job_id,
                source=normalized.source,
                external_id=normalized.external_id,
                url=normalized.url,
                **common_fields,
            )
            .on_conflict_do_update(
                index_elements=[JobSourceRecordModel.source, JobSourceRecordModel.external_id],
                set_={"canonical_job_id": canonical_job_id, **common_fields},
            )
            .returning(JobSourceRecordModel.id)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one()

    # --- retention / reset ---

    async def find_stale_canonical_job_ids(self, cutoff: datetime) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(CanonicalJobModel.id).where(CanonicalJobModel.last_seen_at < cutoff)
        )
        return [row[0] for row in result.all()]

    async def find_source_record_ids(self, canonical_job_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """The source records behind these canonical jobs. Callers need them to
        clear what hangs off a *record* rather than off the canonical job —
        document revisions, in particular — before delete_stale_jobs runs."""
        if not canonical_job_ids:
            return []
        result = await self._session.execute(
            select(JobSourceRecordModel.id).where(
                JobSourceRecordModel.canonical_job_id.in_(canonical_job_ids)
            )
        )
        return list(result.scalars())

    async def delete_stale_jobs(self, canonical_job_ids: list[uuid.UUID]) -> None:
        """Deletes job_source_records for these canonical jobs, then the canonical
        jobs themselves, then any raw_jobs left unreferenced. Must run after
        MatchRepository.delete_for_canonical_jobs — job_matches also reference
        canonical_jobs and would block this otherwise."""
        if not canonical_job_ids:
            return

        raw_job_ids_result = await self._session.execute(
            select(JobSourceRecordModel.raw_job_id).where(
                JobSourceRecordModel.canonical_job_id.in_(canonical_job_ids)
            )
        )
        raw_job_ids = [row[0] for row in raw_job_ids_result.all()]

        await self._session.execute(
            delete(JobSourceRecordModel).where(
                JobSourceRecordModel.canonical_job_id.in_(canonical_job_ids)
            )
        )
        await self._session.execute(
            delete(CanonicalJobModel).where(CanonicalJobModel.id.in_(canonical_job_ids))
        )

        if raw_job_ids:
            still_referenced_result = await self._session.execute(
                select(JobSourceRecordModel.raw_job_id).where(
                    JobSourceRecordModel.raw_job_id.in_(raw_job_ids)
                )
            )
            still_referenced = {row[0] for row in still_referenced_result.all()}
            orphaned = [id_ for id_ in raw_job_ids if id_ not in still_referenced]
            if orphaned:
                await self._session.execute(delete(RawJobModel).where(RawJobModel.id.in_(orphaned)))

        await self._session.flush()

    async def delete_all_jobs(self) -> dict[str, int]:
        """Every vacancy, in dependency order. Callers must have deleted matches
        and their notifications first — see SystemService.reset_jobs."""
        source_records = await self._session.execute(delete(JobSourceRecordModel))
        canonical = await self._session.execute(delete(CanonicalJobModel))
        raw = await self._session.execute(delete(RawJobModel))
        runs = await self._session.execute(delete(ScrapeRunModel))
        await self._session.flush()
        return {
            "job_source_records": rows_affected(source_records),
            "canonical_jobs": rows_affected(canonical),
            "raw_jobs": rows_affected(raw),
            "scrape_runs": rows_affected(runs),
        }
