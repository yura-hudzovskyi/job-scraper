"""Persistence for the Raw -> Normalized -> Canonical job pipeline (docs/domain-model.md).

unique(source, external_id) on raw_jobs and job_source_records makes re-scraping and
re-normalizing idempotent — upserts, never duplicates.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.application import ApplicationModel
from app.db.models.job import (
    CanonicalJobModel,
    JobSourceRecordModel,
    RawJobModel,
    ScrapeRunModel,
)
from app.domain.categories import JobCategory
from app.domain.jobs.models import (
    CanonicalJob,
    EmploymentType,
    JobLocation,
    NormalizedJob,
    NormalizedJobSkill,
    RawJob,
    RequirementType,
    SalaryRange,
)
from app.domain.jobs.scrape_rotation import pick_next_category
from app.domain.versioning import DocumentVersion, job_content_hash


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


def _skills_payload(skills: list[NormalizedJobSkill]) -> list[dict[str, Any]]:
    return [
        {
            "name": skill.name,
            "requirement": skill.requirement.value,
            "canonical_id": skill.canonical_id,
            "evidence": skill.evidence,
            "confidence": skill.confidence,
        }
        for skill in skills
    ]


def _requirement_from_payload(payload: dict[str, Any]) -> RequirementType:
    stored = payload.get("requirement")
    if stored is not None:
        return RequirementType(stored)
    # Rows written before requirement types existed only knew required yes/no.
    return (
        RequirementType.REQUIRED_EXPLICIT
        if payload.get("required")
        else RequirementType.OPTIONAL_EXPLICIT
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
        skills=[
            NormalizedJobSkill(
                name=skill["name"],
                requirement=_requirement_from_payload(skill),
                canonical_id=skill.get("canonical_id"),
                evidence=skill.get("evidence"),
                confidence=skill.get("confidence"),
            )
            for skill in model.skills
        ],
        skills_extracted_by=model.skills_extracted_by,
        category=JobCategory(model.category) if model.category else None,
        category_confidence=model.category_confidence,
    )


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

    async def get_least_recently_scraped_category(self, source: str, categories: list[str]) -> str:
        """Which of `categories` to scrape next for this source — whichever has gone
        longest without a run (or has never been run at all). See scrape_rotation.py
        for the selection logic itself."""
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
        model = ScrapeRunModel(
            source=source,
            category=category,
            started_at=started_at,
            finished_at=finished_at,
            jobs_seen=jobs_seen,
            new_count=new_count,
            errors=errors,
        )
        self._session.add(model)
        await self._session.flush()

    async def get_raw_job(self, raw_job_id: uuid.UUID) -> RawJob:
        model = await self._session.get(RawJobModel, raw_job_id)
        if model is None:
            raise LookupError(f"raw job {raw_job_id} not found")
        return _to_raw_job(model)

    async def count_canonical_jobs(self, exclude_ids: set[uuid.UUID] | None = None) -> int:
        stmt = select(func.count()).select_from(CanonicalJobModel)
        if exclude_ids:
            stmt = stmt.where(CanonicalJobModel.id.notin_(exclude_ids))
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def list_all_canonical_job_ids(self) -> list[uuid.UUID]:
        """Every canonical job id, no join — used to fan out backfill scoring for a
        newly-onboarded user (see workers/tasks/backfill.py) without paying for the
        source-records join list_canonical_jobs does for the full jobs-list view."""
        result = await self._session.execute(select(CanonicalJobModel.id))
        return list(result.scalars())

    async def list_canonical_jobs(
        self,
        limit: int | None = None,
        offset: int = 0,
        exclude_ids: set[uuid.UUID] | None = None,
    ) -> list[CanonicalJob]:
        """Canonical jobs, newest-seen first. `limit=None` (the default) returns every
        row — used by DeduplicationService, which needs the full candidate set. The
        jobs list API must always pass a real `limit`; without one, every page load
        would pull the entire table (and, before pagination existed, the frontend
        additionally fired one match request per row on top of that — see
        docs/api.md and Jobs.tsx). `exclude_ids` lets the jobs-list API hide jobs
        already recommendation=SKIP for the current user by default (see
        JobService.list_jobs) — omitted (or empty) entirely skips the clause rather
        than filtering on an empty NOT IN, which is both pointless and edge-case-prone."""
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

    async def get_normalized_job_for_canonical(
        self, canonical_job_id: uuid.UUID
    ) -> NormalizedJob | None:
        """The full NormalizedJob (salary, location, skills, ...) for one of this
        canonical job's source records — canonical_jobs itself only stores the
        title/company/description subset used for dedup matching. Picks whichever
        source record was normalized most recently."""
        result = await self._session.execute(
            select(JobSourceRecordModel)
            .where(JobSourceRecordModel.canonical_job_id == canonical_job_id)
            .order_by(JobSourceRecordModel.normalized_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return _to_normalized_job(model) if model else None

    async def list_source_links_for_canonical(
        self, canonical_job_id: uuid.UUID
    ) -> list[tuple[str, str]]:
        """(source, url) pairs for every source this canonical job is known under —
        used by the Telegram notification to link out to DOU/Djinni/etc. by name
        instead of a single, arbitrarily-chosen URL. One row per source (most
        recently normalized wins if a source somehow has more than one record for
        the same canonical job)."""
        result = await self._session.execute(
            select(JobSourceRecordModel.source, JobSourceRecordModel.url)
            .where(JobSourceRecordModel.canonical_job_id == canonical_job_id)
            .order_by(JobSourceRecordModel.normalized_at.desc())
        )
        links: dict[str, str] = {}
        for source, url in result.all():
            links.setdefault(source, url)
        return list(links.items())

    async def update_skills_for_canonical(
        self,
        canonical_job_id: uuid.UUID,
        skills: list[NormalizedJobSkill],
        generated_by: str | None,
        category: JobCategory | None = None,
        category_confidence: float | None = None,
    ) -> None:
        """Saves extracted requirements onto whichever source record scoring reads via
        get_normalized_job_for_canonical — same "most recently normalized" selection,
        so extraction writes to exactly the row matching later reads from. The
        category comes from the same extraction call, so it is written here rather
        than costing a second pass over the posting."""
        result = await self._session.execute(
            select(JobSourceRecordModel)
            .where(JobSourceRecordModel.canonical_job_id == canonical_job_id)
            .order_by(JobSourceRecordModel.normalized_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return
        model.skills = _skills_payload(skills)
        model.skills_extracted_by = generated_by
        if category is not None:
            model.category = category.value
            model.category_confidence = category_confidence
        await self._session.flush()

    async def refresh_canonical_content_version(
        self, canonical_job_id: uuid.UUID
    ) -> DocumentVersion | None:
        """Recompute this vacancy's content identity from the source record scoring
        actually reads, bumping its version when the analysis-relevant content
        changed since last time (see app/domain/versioning.py). Called from the
        scoring path — the one place that needs the identity — so it is always
        current for the result being produced, and a job stored before hashing
        existed heals on its next score. Returns None for a canonical job with no
        source record to read."""
        normalized = await self.get_normalized_job_for_canonical(canonical_job_id)
        model = await self._session.get(CanonicalJobModel, canonical_job_id)
        if normalized is None or model is None:
            return None

        new_hash = job_content_hash(normalized)
        if model.content_hash != new_hash:
            # A first hash isn't a new version — it's the same posting, finally
            # identified. Only a *changed* hash means the content moved.
            if model.content_hash is not None:
                model.content_version += 1
            model.content_hash = new_hash
            await self._session.flush()
        return DocumentVersion(version=model.content_version, content_hash=new_hash)

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
            "skills": _skills_payload(normalized.skills),
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

    async def find_stale_canonical_job_ids(self, cutoff: datetime) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(CanonicalJobModel.id).where(CanonicalJobModel.last_seen_at < cutoff)
        )
        return [row[0] for row in result.all()]

    async def delete_stale_jobs(self, canonical_job_ids: list[uuid.UUID]) -> None:
        """Deletes applications and job_source_records for these canonical jobs, then
        the canonical_jobs themselves, then any raw_jobs left unreferenced by that.
        Must run after MatchRepository.delete_for_canonical_jobs — job_matches also
        reference canonical_jobs and would block this otherwise. See
        JobRetentionService for the full cross-table ordering."""
        if not canonical_job_ids:
            return

        await self._session.execute(
            delete(ApplicationModel).where(ApplicationModel.canonical_job_id.in_(canonical_job_ids))
        )

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
            orphaned = [raw_job_id for raw_job_id in raw_job_ids if raw_job_id not in still_referenced]
            if orphaned:
                await self._session.execute(delete(RawJobModel).where(RawJobModel.id.in_(orphaned)))

        await self._session.flush()
