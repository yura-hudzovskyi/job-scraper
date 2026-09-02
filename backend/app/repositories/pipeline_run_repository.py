"""Persistence for pipeline run records — what the System page's status and
history read.

Steps are appended as the run progresses rather than written once at the end, so
a UI polling this sees where a long run actually is instead of a spinner.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.pipeline import PipelineRunModel
from app.repositories.base import rows_affected

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"


@dataclass(frozen=True)
class PipelineRun:
    id: str
    trigger: str
    status: str
    steps: list[dict[str, Any]]
    error: str | None
    started_at: datetime
    finished_at: datetime | None


def _to_run(model: PipelineRunModel) -> PipelineRun:
    return PipelineRun(
        id=str(model.id),
        trigger=model.trigger,
        status=model.status,
        steps=list(model.steps or []),
        error=model.error,
        started_at=model.started_at,
        finished_at=model.finished_at,
    )


class PipelineRunRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def start(self, trigger: str) -> uuid.UUID:
        model = PipelineRunModel(trigger=trigger, status=RUNNING, steps=[])
        self._session.add(model)
        await self._session.flush()
        return model.id

    async def add_step(self, run_id: uuid.UUID, name: str, detail: dict[str, Any]) -> None:
        model = await self._session.get(PipelineRunModel, run_id)
        if model is None:
            return
        # Reassigned rather than mutated in place: SQLAlchemy doesn't track
        # in-place edits to a JSONB list, so an appended step would never be
        # written and the progress view would stay empty until the run ended.
        model.steps = [*(model.steps or []), {"name": name, **detail}]
        await self._session.flush()

    async def finish(
        self, run_id: uuid.UUID, status: str, error: str | None = None
    ) -> None:
        model = await self._session.get(PipelineRunModel, run_id)
        if model is None:
            return
        model.status = status
        model.error = error
        model.finished_at = datetime.now(UTC)
        await self._session.flush()

    async def latest(self, limit: int = 10) -> list[PipelineRun]:
        result = await self._session.execute(
            select(PipelineRunModel).order_by(PipelineRunModel.started_at.desc()).limit(limit)
        )
        return [_to_run(model) for model in result.scalars()]

    async def active(self) -> PipelineRun | None:
        """The run currently in progress, if any — what stops the UI offering a
        second "Run pipeline" while the first is still going."""
        result = await self._session.execute(
            select(PipelineRunModel)
            .where(PipelineRunModel.status == RUNNING)
            .order_by(PipelineRunModel.started_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return _to_run(model) if model else None

    async def delete_all(self) -> int:
        result = await self._session.execute(delete(PipelineRunModel))
        await self._session.flush()
        return rows_affected(result)
