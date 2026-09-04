"""Persistence for document revisions, their blocks and their status history.

Two rules this module exists to enforce, mirroring EmbeddingRepository's:
re-ingesting unchanged text creates nothing, and a revision never moves to a
status it cannot legally reach. The first is enforced twice on purpose — by
`plan_revision` before the insert, and by `unique(owner, content_hash)` if a
caller skips the plan.

The decisions themselves live in app/domain/documents/models.py so they can be
tested without a database; this class does the SQL and the auditing.
"""

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.db.models.document import (
    DocumentBlockModel,
    DocumentRevisionModel,
    DocumentRevisionTransitionModel,
)
from app.db.models.profile import ProfileRevisionModel
from app.domain.documents.models import (
    BlockType,
    DocumentRevision,
    EntityKind,
    RevisionRef,
    RevisionStatus,
    check_transition,
    plan_revision,
)
from app.repositories.base import rows_affected


def _owner_column(entity_kind: EntityKind) -> InstrumentedAttribute[uuid.UUID | None]:
    return (
        DocumentRevisionModel.job_source_record_id
        if entity_kind is EntityKind.JOB
        else DocumentRevisionModel.cv_document_id
    )


def _to_domain(model: DocumentRevisionModel) -> DocumentRevision:
    owner = model.job_source_record_id or model.cv_document_id
    if owner is None:  # pragma: no cover - ck_document_revisions_exactly_one_owner
        raise ValueError(f"document revision {model.id} has no owner")
    return DocumentRevision(
        id=str(model.id),
        entity_kind=EntityKind(model.entity_kind),
        owner_id=str(owner),
        revision_no=model.revision_no,
        content_hash=model.content_hash,
        status=RevisionStatus(model.status),
        raw_text=model.raw_text,
        parsed_text=model.parsed_text,
        language_code=model.language_code,
        mime_type=model.mime_type,
        parser_name=model.parser_name,
        parser_version=model.parser_version,
        failure_code=model.failure_code,
        failure_detail=model.failure_detail,
        created_at=model.created_at,
    )


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def history(self, entity_kind: EntityKind, owner_id: uuid.UUID) -> list[RevisionRef]:
        """Every revision number and content hash stored for this document — the
        input `plan_revision` needs to decide what a re-ingest does."""
        result = await self._session.execute(
            select(DocumentRevisionModel.revision_no, DocumentRevisionModel.content_hash).where(
                _owner_column(entity_kind) == owner_id
            )
        )
        return [RevisionRef(revision_no=row[0], content_hash=row[1]) for row in result.all()]

    async def latest(
        self, entity_kind: EntityKind, owner_id: uuid.UUID
    ) -> DocumentRevision | None:
        result = await self._session.execute(
            select(DocumentRevisionModel)
            .where(_owner_column(entity_kind) == owner_id)
            .order_by(DocumentRevisionModel.revision_no.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def get(self, revision_id: uuid.UUID) -> DocumentRevision | None:
        model = await self._session.get(DocumentRevisionModel, revision_id)
        return _to_domain(model) if model is not None else None

    async def record(
        self,
        entity_kind: EntityKind,
        owner_id: uuid.UUID,
        content_hash: str,
        raw_text: str,
        mime_type: str | None = None,
        language_code: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> tuple[DocumentRevision, bool]:
        """Store this content as a revision if it is new.

        Returns `(revision, created)`. `created` False means the exact text was
        already stored — the caller has nothing to reprocess, which is the normal
        outcome of re-scraping a vacancy that has not changed.
        """
        plan = plan_revision(await self.history(entity_kind, owner_id), content_hash)
        if not plan.is_new:
            existing = await self._session.execute(
                select(DocumentRevisionModel).where(
                    _owner_column(entity_kind) == owner_id,
                    DocumentRevisionModel.revision_no == plan.revision_no,
                )
            )
            return _to_domain(existing.scalar_one()), False

        model = DocumentRevisionModel(
            entity_kind=entity_kind.value,
            job_source_record_id=owner_id if entity_kind is EntityKind.JOB else None,
            cv_document_id=owner_id if entity_kind is EntityKind.CANDIDATE else None,
            revision_no=plan.revision_no,
            content_hash=content_hash,
            raw_text=raw_text,
            mime_type=mime_type,
            language_code=language_code,
            raw_payload=raw_payload,
            status=RevisionStatus.RECEIVED.value,
        )
        self._session.add(model)
        await self._session.flush()
        self._session.add(
            DocumentRevisionTransitionModel(
                document_revision_id=model.id,
                from_status=None,
                to_status=RevisionStatus.RECEIVED.value,
                reason="ingested",
            )
        )
        await self._session.flush()
        return _to_domain(model), True

    async def transition(
        self,
        revision_id: uuid.UUID,
        target: RevisionStatus,
        reason: str | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
    ) -> DocumentRevision:
        """Move a revision to `target`, refusing an illegal jump and recording the
        move when it succeeds.

        Raises IllegalTransition rather than writing an unreachable state — see
        app/domain/documents/models.py for why that is worth failing loudly over.
        """
        model = await self._session.get(DocumentRevisionModel, revision_id)
        if model is None:
            raise LookupError(f"no document revision {revision_id}")

        current = RevisionStatus(model.status)
        check_transition(current, target)

        model.status = target.value
        if target is RevisionStatus.FAILED:
            model.failure_code = failure_code
            model.failure_detail = failure_detail
        else:
            # Leaving FAILED clears the previous failure: keeping it would make a
            # recovered revision look broken on the admin screen forever.
            model.failure_code = None
            model.failure_detail = None

        self._session.add(
            DocumentRevisionTransitionModel(
                document_revision_id=model.id,
                from_status=current.value,
                to_status=target.value,
                reason=reason or failure_code,
            )
        )
        await self._session.flush()
        return _to_domain(model)

    async def transitions(
        self, revision_id: uuid.UUID
    ) -> list[tuple[str | None, str, str | None]]:
        """(from, to, reason) in the order they happened — what an admin screen
        shows when someone asks how a revision reached `failed`."""
        result = await self._session.execute(
            select(
                DocumentRevisionTransitionModel.from_status,
                DocumentRevisionTransitionModel.to_status,
                DocumentRevisionTransitionModel.reason,
            )
            .where(DocumentRevisionTransitionModel.document_revision_id == revision_id)
            .order_by(DocumentRevisionTransitionModel.occurred_at)
        )
        return [(row[0], row[1], row[2]) for row in result.all()]

    async def store_parse(
        self,
        revision_id: uuid.UUID,
        parsed_text: str,
        blocks: list[tuple[int, BlockType, str, int, int]],
        parser_name: str,
        parser_version: str,
        language_code: str | None = None,
    ) -> int:
        """The parsed text and the blocks whose offsets index into it, together.

        One method rather than two because they are one fact. Blocks written
        against a `parsed_text` that was not updated in the same breath have
        offsets pointing into the previous parse, and every evidence span built
        on them would quote the wrong substring — silently, since the offsets
        would still be in range.

        `language_code` belongs here rather than on `record` because it is
        detected from the parsed text: raw markup carries Latin tag names that
        outvote the body of a short Ukrainian vacancy.

        Replaces rather than appends, for the same reason as above: re-parsing
        under a new parser version must not leave the old parse's blocks behind.
        """
        revision = await self._session.get(DocumentRevisionModel, revision_id)
        if revision is None:
            raise LookupError(f"no document revision {revision_id}")
        revision.parsed_text = parsed_text
        revision.parser_name = parser_name
        revision.parser_version = parser_version
        if language_code is not None:
            revision.language_code = language_code

        await self._session.execute(
            delete(DocumentBlockModel).where(
                DocumentBlockModel.document_revision_id == revision_id
            )
        )
        for ordinal, block_type, text, start_char, end_char in blocks:
            self._session.add(
                DocumentBlockModel(
                    document_revision_id=revision_id,
                    ordinal=ordinal,
                    block_type=block_type.value,
                    text=text,
                    start_char=start_char,
                    end_char=end_char,
                )
            )
        await self._session.flush()
        return len(blocks)

    async def delete_for_owners(
        self, entity_kind: EntityKind, owner_ids: list[uuid.UUID]
    ) -> dict[str, int]:
        """Everything hanging off these documents, in dependency order.

        Called before the owning rows are deleted — see JobRetentionService and
        SystemService.reset_jobs. Nothing in the schema cascades, so this is the
        only thing standing between a purge and a foreign key violation.
        """
        if not owner_ids:
            return {}
        result = await self._session.execute(
            select(DocumentRevisionModel.id).where(_owner_column(entity_kind).in_(owner_ids))
        )
        return await self._delete_revisions(list(result.scalars()))

    async def delete_for_kind(self, entity_kind: EntityKind) -> dict[str, int]:
        """Every revision of one entity kind. Used by the System page's reset
        actions, which wipe vacancies but deliberately keep the account — so this
        is scoped by kind rather than offering a `delete_all` that would take the
        candidate's CV history with it."""
        result = await self._session.execute(
            select(DocumentRevisionModel.id).where(
                DocumentRevisionModel.entity_kind == entity_kind.value
            )
        )
        return await self._delete_revisions(list(result.scalars()))

    async def count(self, entity_kind: EntityKind | None = None) -> int:
        stmt = select(DocumentRevisionModel.id)
        if entity_kind is not None:
            stmt = stmt.where(DocumentRevisionModel.entity_kind == entity_kind.value)
        result = await self._session.execute(stmt)
        return len(list(result.scalars()))

    async def _delete_revisions(self, revision_ids: list[uuid.UUID]) -> dict[str, int]:
        if not revision_ids:
            return {}
        profiles = await self._session.execute(
            delete(ProfileRevisionModel).where(
                ProfileRevisionModel.document_revision_id.in_(revision_ids)
            )
        )
        transitions = await self._session.execute(
            delete(DocumentRevisionTransitionModel).where(
                DocumentRevisionTransitionModel.document_revision_id.in_(revision_ids)
            )
        )
        blocks = await self._session.execute(
            delete(DocumentBlockModel).where(
                DocumentBlockModel.document_revision_id.in_(revision_ids)
            )
        )
        revisions = await self._session.execute(
            delete(DocumentRevisionModel).where(DocumentRevisionModel.id.in_(revision_ids))
        )
        await self._session.flush()
        return {
            "profile_revisions": rows_affected(profiles),
            "document_revision_transitions": rows_affected(transitions),
            "document_blocks": rows_affected(blocks),
            "document_revisions": rows_affected(revisions),
        }
