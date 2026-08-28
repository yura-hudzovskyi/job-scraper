import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models in app/db/models/.

    Every Mapped[datetime] column gets TIMESTAMPTZ rather than a naive timestamp —
    the app stores everything as timezone-aware UTC (datetime.now(UTC)), and asyncpg
    (unlike psycopg's sync driver) rejects mixing aware datetimes into a naive column
    outright rather than silently truncating.
    """

    # SQLAlchemy's declarative metaclass reads this as a plain class dict — ClassVar
    # would defeat its own special-cased attribute processing.
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
