"""Durable storage for the AI call ledger. Written only by the flush task
(app/workers/tasks/ai_ledger.py), which drains the Redis buffer the router
writes to — see app/integrations/ai/quota/ledger.py for why the hot path doesn't
touch Postgres.
"""

from datetime import datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ai import AiInvocationModel
from app.integrations.ai.quota.ledger import InvocationRecord


class AiInvocationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def insert_many(self, records: list[InvocationRecord]) -> int:
        if not records:
            return 0
        await self._session.execute(
            insert(AiInvocationModel),
            [
                {
                    "capability": record.capability,
                    "provider": record.provider,
                    "model": record.model,
                    "outcome": record.outcome,
                    "status": record.status,
                    "latency_ms": record.latency_ms,
                    "prompt_chars": record.prompt_chars,
                    **({"created_at": record.at} if record.at else {}),
                }
                for record in records
            ],
        )
        await self._session.flush()
        return len(records)

    async def delete_older_than(self, cutoff: datetime) -> int:
        """History past the tuning window answers no question this app asks, and
        this table grows with every call the router makes."""
        result = await self._session.execute(
            delete(AiInvocationModel).where(AiInvocationModel.created_at < cutoff)
        )
        await self._session.flush()
        return result.rowcount or 0

    async def count_since(self, since: datetime) -> dict[tuple[str, str], int]:
        """Calls per (capability, outcome) since a point in time — what a usage
        report is built from."""
        result = await self._session.execute(
            select(AiInvocationModel.capability, AiInvocationModel.outcome, func.count())
            .where(AiInvocationModel.created_at >= since)
            .group_by(AiInvocationModel.capability, AiInvocationModel.outcome)
        )
        return {(capability, outcome): count for capability, outcome, count in result.all()}
