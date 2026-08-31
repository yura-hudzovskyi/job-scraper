"""ORM tables for Telegram integration credentials and Notification/NotificationDelivery.

unique(notification_id, channel) keeps delivery idempotent — see docs/notifications.md.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class TelegramIntegrationModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "telegram_integrations"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    bot_token: Mapped[str]
    chat_id: Mapped[str]
    connected_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NotificationModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    job_match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_matches.id"))
    channel: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NotificationDeliveryModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("notification_id", "channel"),)

    notification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notifications.id"))
    channel: Mapped[str]
    delivered_at: Mapped[datetime | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(default=None)


class NotificationSettingsModel(UUIDPrimaryKeyMixin, Base):
    """Per-user override of NotificationPolicyConfig's defaults (see
    app/domain/notifications/policy.py) — one row per user, created on first save
    from the Settings page. Absence of a row means "use NotificationPolicyConfig()'s
    hardcoded defaults," not "notifications disabled" — see
    NotificationRepository.get_notification_policy_config.
    """

    __tablename__ = "notification_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    immediate_threshold: Mapped[float] = mapped_column(default=85.0)
    conditional_threshold: Mapped[float] = mapped_column(default=75.0)
    digest_threshold: Mapped[float] = mapped_column(default=65.0)
    strong_component_threshold: Mapped[float] = mapped_column(default=90.0)
    quiet_hours_start: Mapped[int] = mapped_column(default=22)
    quiet_hours_end: Mapped[int] = mapped_column(default=8)
