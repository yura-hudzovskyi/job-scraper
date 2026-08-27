"""Persistence for the Raw -> Normalized -> Canonical job pipeline (docs/domain-model.md).

unique(source, external_id) on raw_jobs and job_source_records makes re-scraping and
re-normalizing idempotent — upserts, never duplicates.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job import CanonicalJobModel, JobSourceRecordModel, RawJobModel
from app.domain.jobs.models import CanonicalJob, EmploymentType, JobLocation, NormalizedJob, RawJob


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
        salary=None,
        seniority=None,
        required_experience_years=None,
    )


def _skills_payload(normalized: NormalizedJob) -> list[dict[str, Any]]:
    return [{"name": skill.name, "required": skill.required} for skill in normalized.skills]


class JobRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

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

    async def get_raw_job(self, raw_job_id: uuid.UUID) -> RawJob:
        model = await self._session.get(RawJobModel, raw_job_id)
        if model is None:
            raise LookupError(f"raw job {raw_job_id} not found")
        return _to_raw_job(model)

    async def list_canonical_jobs(self) -> list[CanonicalJob]:
        """All canonical jobs, as dedup candidates and for the jobs list API. Fine at
        Phase 1 (personal-scale) volumes — narrowing by company/recency is a later
        optimization if this ever needs to scale past that."""
        result = await self._session.execute(select(CanonicalJobModel))
        canonical_models = result.scalars().all()
        if not canonical_models:
            return []

        source_records_result = await self._session.execute(
            select(JobSourceRecordModel.canonical_job_id, JobSourceRecordModel.id).where(
                JobSourceRecordModel.canonical_job_id.in_([m.id for m in canonical_models])
            )
        )
        source_record_ids_by_canonical: dict[uuid.UUID, list[str]] = {}
        for canonical_id, source_record_id in source_records_result.all():
            source_record_ids_by_canonical.setdefault(canonical_id, []).append(str(source_record_id))

        return [
            CanonicalJob(
                id=str(model.id),
                normalized=_canonical_candidate_view(model),
                source_records=source_record_ids_by_canonical.get(model.id, []),
            )
            for model in canonical_models
        ]

    async def get_canonical_job(self, canonical_job_id: uuid.UUID) -> CanonicalJob | None:
        model = await self._session.get(CanonicalJobModel, canonical_job_id)
        if model is None:
            return None
        source_records_result = await self._session.execute(
            select(JobSourceRecordModel.id).where(
                JobSourceRecordModel.canonical_job_id == canonical_job_id
            )
        )
        source_record_ids = [str(row[0]) for row in source_records_result.all()]
        return CanonicalJob(
            id=str(model.id), normalized=_canonical_candidate_view(model), source_records=source_record_ids
        )

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
        """Upsert the JobSourceRecord for (source, external_id), attached to canonical_job_id."""
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
            "skills": _skills_payload(normalized),
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
