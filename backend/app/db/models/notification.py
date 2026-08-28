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
