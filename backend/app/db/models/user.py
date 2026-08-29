"""The root entity everything else (profile, preferences, jobs actions) hangs off."""

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class UserModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
