"""Persistence for extracted profile revisions.

Append-only, like the document side and for the same reason: a match scored
against a profile has to stay explainable after the candidate corrects their
skills. `current` is therefore the newest row rather than a mutable pointer.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.profile import ProfileRevisionModel
from app.domain.profiles.models import ProfileKind, ProfileOrigin, ProfileRevision


def _to_domain(model: ProfileRevisionModel) -> ProfileRevision:
    return ProfileRevision(
        id=str(model.id),
        document_revision_id=str(model.document_revision_id),
        profile_kind=ProfileKind(model.profile_kind),
        schema_version=model.schema_version,
        origin=ProfileOrigin(model.origin),
        extracted_profile=model.extracted_profile,
        parent_revision_id=(
            str(model.parent_revision_id) if model.parent_revision_id else None
        ),
        extractor_model_id=model.extractor_model_id,
        overall_confidence=model.overall_confidence,
        validation_warnings=model.validation_warnings,
        created_at=model.created_at,
    )


class ProfileRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(
        self,
        document_revision_id: uuid.UUID,
        profile_kind: ProfileKind,
        schema_version: str,
        origin: ProfileOrigin,
        extracted_profile: dict[str, Any],
        extractor_model_id: str | None = None,
        overall_confidence: float | None = None,
        validation_warnings: list[dict[str, Any]] | None = None,
        parent_revision_id: uuid.UUID | None = None,
    ) -> ProfileRevision:
        """Append a profile revision.

        Refuses an automated origin with nothing naming what produced it, before
        the database refuses it — the error is the same either way, but this one
        says which call site did it.
        """
        if origin.is_automated and not extractor_model_id:
            raise ValueError(
                f"a {origin} profile revision must name the extractor that produced it, "
                "or it cannot be reproduced"
            )

        model = ProfileRevisionModel(
            document_revision_id=document_revision_id,
            profile_kind=profile_kind.value,
            schema_version=schema_version,
            origin=origin.value,
            extracted_profile=extracted_profile,
            extractor_model_id=extractor_model_id,
            overall_confidence=overall_confidence,
            validation_warnings=validation_warnings or [],
            parent_revision_id=parent_revision_id,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_domain(model)

    async def current(self, document_revision_id: uuid.UUID) -> ProfileRevision | None:
        """The newest profile for this document — what a reader should use."""
        result = await self._session.execute(
            select(ProfileRevisionModel)
            .where(ProfileRevisionModel.document_revision_id == document_revision_id)
            .order_by(ProfileRevisionModel.created_at.desc(), ProfileRevisionModel.id.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def history(self, document_revision_id: uuid.UUID) -> list[ProfileRevision]:
        """Every profile for this document, oldest first — the extraction and
        every correction made to it."""
        result = await self._session.execute(
            select(ProfileRevisionModel)
            .where(ProfileRevisionModel.document_revision_id == document_revision_id)
            .order_by(ProfileRevisionModel.created_at, ProfileRevisionModel.id)
        )
        return [_to_domain(model) for model in result.scalars()]

    async def get(self, profile_revision_id: uuid.UUID) -> ProfileRevision | None:
        model = await self._session.get(ProfileRevisionModel, profile_revision_id)
        return _to_domain(model) if model is not None else None
