"""Persistence for linked concept mentions and the unmapped queue."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.taxonomy import ProfileConceptMentionModel, UnmappedMentionModel


class MentionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def replace_for_profile(
        self, profile_revision_id: uuid.UUID, rows: list[dict[str, Any]]
    ) -> int:
        """Mentions for one profile revision, replacing any previous run.

        Replaces rather than appends for the same reason block parsing does:
        re-linking under a new taxonomy version must not leave the old version's
        concept ids sitting alongside the new ones.
        """
        await self._session.execute(
            delete(ProfileConceptMentionModel).where(
                ProfileConceptMentionModel.profile_revision_id == profile_revision_id
            )
        )
        if rows:
            await self._session.execute(insert(ProfileConceptMentionModel), rows)
        await self._session.flush()
        return len(rows)

    async def record_unmapped(self, terms: list[tuple[str, str]]) -> None:
        """Count terms the taxonomy did not cover.

        Spec 9.4: an unknown mention must not silently become a concept. It is
        counted instead, and frequency is the whole value — seen once is probably
        a typo, seen four hundred times is a gap worth a person's attention.

        Upserted rather than read-then-written so two workers linking different
        documents cannot lose each other's increment.
        """
        if not terms:
            return
        now = datetime.now(UTC)
        for normalized, raw in terms:
            await self._session.execute(
                pg_insert(UnmappedMentionModel)
                .values(
                    normalized_text=normalized,
                    sample_raw_text=raw,
                    occurrences=1,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[UnmappedMentionModel.normalized_text],
                    set_={
                        "occurrences": UnmappedMentionModel.occurrences + 1,
                        "last_seen_at": now,
                    },
                )
            )
        await self._session.flush()

    async def pending_unmapped(self, limit: int = 50) -> list[tuple[str, str, int]]:
        """The most frequent terms awaiting review, commonest first — what a
        person should look at when deciding whether the taxonomy has a gap."""
        result = await self._session.execute(
            select(
                UnmappedMentionModel.normalized_text,
                UnmappedMentionModel.sample_raw_text,
                UnmappedMentionModel.occurrences,
            )
            .where(UnmappedMentionModel.status == "pending")
            .order_by(UnmappedMentionModel.occurrences.desc())
            .limit(limit)
        )
        return [(row[0], row[1], row[2]) for row in result.all()]

    async def review_unmapped(self, normalized_text: str, status: str) -> bool:
        """Mark a term promoted or ignored. False when there is no such term."""
        model = await self._session.get(UnmappedMentionModel, normalized_text)
        if model is None:
            return False
        model.status = status
        model.reviewed_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def count_by_status(self, profile_revision_id: uuid.UUID) -> dict[str, int]:
        result = await self._session.execute(
            select(ProfileConceptMentionModel.link_status).where(
                ProfileConceptMentionModel.profile_revision_id == profile_revision_id
            )
        )
        counts: dict[str, int] = {}
        for (status,) in result.all():
            counts[status] = counts.get(status, 0) + 1
        return counts
