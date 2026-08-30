"""Persistence for Telegram integration credentials and notification delivery
records. Delivery is upserted on (notification_id, channel), so a retry after a
failed attempt overwrites that one row instead of duplicating — see
docs/notifications.md.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.notification import (
    NotificationDeliveryModel,
    NotificationModel,
    TelegramIntegrationModel,
)


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save_telegram_integration(
        self, user_id: uuid.UUID, bot_token: str, chat_id: str
    ) -> None:
        stmt = (
            insert(TelegramIntegrationModel)
            .values(user_id=user_id, bot_token=bot_token, chat_id=chat_id)
            .on_conflict_do_update(
                index_elements=[TelegramIntegrationModel.user_id],
                set_={"bot_token": bot_token, "chat_id": chat_id},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_telegram_integration(self, user_id: uuid.UUID) -> tuple[str, str] | None:
        result = await self._session.execute(
            select(TelegramIntegrationModel.bot_token, TelegramIntegrationModel.chat_id).where(
                TelegramIntegrationModel.user_id == user_id
            )
        )
        row = result.first()
        return (row[0], row[1]) if row else None

    async def get_or_create_notification(
        self, user_id: uuid.UUID, job_match_id: uuid.UUID, channel: str
    ) -> uuid.UUID:
        result = await self._session.execute(
            select(NotificationModel.id).where(
                NotificationModel.user_id == user_id,
                NotificationModel.job_match_id == job_match_id,
                NotificationModel.channel == channel,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        model = NotificationModel(user_id=user_id, job_match_id=job_match_id, channel=channel)
        self._session.add(model)
        await self._session.flush()
        return model.id

    async def has_successful_delivery(self, notification_id: uuid.UUID, channel: str) -> bool:
        result = await self._session.execute(
            select(NotificationDeliveryModel.delivered_at).where(
                NotificationDeliveryModel.notification_id == notification_id,
                NotificationDeliveryModel.channel == channel,
            )
        )
        delivered_at = result.scalar_one_or_none()
        return delivered_at is not None

    async def delete_for_job_matches(self, job_match_ids: list[uuid.UUID]) -> None:
        """Call before deleting the job_matches themselves — notification_deliveries
        reference notifications, which reference job_matches, so both must go first."""
        if not job_match_ids:
            return
        notification_ids_result = await self._session.execute(
            select(NotificationModel.id).where(NotificationModel.job_match_id.in_(job_match_ids))
        )
        notification_ids = [row[0] for row in notification_ids_result.all()]

        if notification_ids:
            await self._session.execute(
                delete(NotificationDeliveryModel).where(
                    NotificationDeliveryModel.notification_id.in_(notification_ids)
                )
            )
        await self._session.execute(
            delete(NotificationModel).where(NotificationModel.job_match_id.in_(job_match_ids))
        )
        await self._session.flush()

    async def record_delivery(
        self, notification_id: uuid.UUID, channel: str, error: str | None = None
    ) -> None:
        values = {
            "delivered_at": None if error else datetime.now(UTC),
            "error": error,
        }
        stmt = (
            insert(NotificationDeliveryModel)
            .values(notification_id=notification_id, channel=channel, **values)
            .on_conflict_do_update(
                index_elements=[
                    NotificationDeliveryModel.notification_id,
                    NotificationDeliveryModel.channel,
                ],
                set_=values,
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
