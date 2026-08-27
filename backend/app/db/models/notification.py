"""ORM table for Notification/NotificationDelivery.

unique(notification_id, channel) keeps delivery idempotent — see docs/notifications.md.
The parent `notifications` table itself is a Phase 3 concern (docs/roadmap.md).
"""

import uuid

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class NotificationDeliveryModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("notification_id", "channel"),)

    notification_id: Mapped[uuid.UUID]
    channel: Mapped[str]
