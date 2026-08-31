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
    NotificationSettingsModel,
    TelegramIntegrationModel,
)
from app.domain.notifications.policy import NotificationPolicyConfig


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

    async def get_user_id_for_chat_id(self, chat_id: str) -> uuid.UUID | None:
        """Reverse lookup for incoming Telegram updates (see the webhook route in
        api/routes/telegram.py) — a callback_query only carries the chat id,
        never our internal user id. All users share one bot token, so chat_id
        alone is enough to disambiguate."""
        result = await self._session.execute(
            select(TelegramIntegrationModel.user_id).where(
                TelegramIntegrationModel.chat_id == chat_id
            )
        )
        return result.scalar_one_or_none()

    async def get_notification_policy_config(self, user_id: uuid.UUID) -> NotificationPolicyConfig:
        """Always returns a usable config — NotificationPolicyConfig()'s hardcoded
        defaults when the user has never saved a row, their saved thresholds
        otherwise. See app/api/routes/settings.py's GET/PATCH /api/settings/notifications
        and app/workers/tasks/notify.py, which builds NotificationPolicy from this."""
        result = await self._session.execute(
            select(NotificationSettingsModel).where(NotificationSettingsModel.user_id == user_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return NotificationPolicyConfig()
        return NotificationPolicyConfig(
            immediate_threshold=model.immediate_threshold,
            conditional_threshold=model.conditional_threshold,
            digest_threshold=model.digest_threshold,
            strong_component_threshold=model.strong_component_threshold,
            quiet_hours_start=model.quiet_hours_start,
            quiet_hours_end=model.quiet_hours_end,
        )

    async def save_notification_policy_config(
        self, user_id: uuid.UUID, config: NotificationPolicyConfig
    ) -> NotificationPolicyConfig:
        result = await self._session.execute(
            select(NotificationSettingsModel).where(NotificationSettingsModel.user_id == user_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            model = NotificationSettingsModel(user_id=user_id)
            self._session.add(model)

        model.immediate_threshold = config.immediate_threshold
        model.conditional_threshold = config.conditional_threshold
        model.digest_threshold = config.digest_threshold
        model.strong_component_threshold = config.strong_component_threshold
        model.quiet_hours_start = config.quiet_hours_start
        model.quiet_hours_end = config.quiet_hours_end

        await self._session.flush()
        return config

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
