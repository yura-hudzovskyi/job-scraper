"""Reads and writes the one PipelineConfig row."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.settings import PipelineConfigModel
from app.domain.pipeline_config import DEFAULTS, PipelineConfig

_ROW_ID = 1


class PipelineConfigRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self) -> PipelineConfig:
        """Never fails and never returns None: an install that has saved nothing
        runs on DEFAULTS, which is also exactly what the row would contain."""
        model = await self._session.get(PipelineConfigModel, _ROW_ID)
        if model is None:
            return DEFAULTS
        return PipelineConfig(
            **{name: getattr(model, name) for name in DEFAULTS.as_dict()}
        )

    async def save(self, config: PipelineConfig) -> PipelineConfig:
        model = await self._session.get(PipelineConfigModel, _ROW_ID)
        if model is None:
            model = PipelineConfigModel(id=_ROW_ID)
            self._session.add(model)
        for name, value in config.as_dict().items():
            setattr(model, name, value)
        model.updated_at = datetime.now(UTC)
        await self._session.flush()
        return config
