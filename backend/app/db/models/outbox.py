"""ORM table for the transactional outbox — spec 16.

The problem it solves is narrow and real: a state change is committed to
Postgres, then an event about it is published to Redis. Those are two systems,
so between them the process can die, and the event is lost with no trace that it
ever should have existed. Writing the event into the same Postgres transaction
as the state change removes the gap — either both happen or neither does — and a
relay moves it to the queue afterwards, retrying until it sticks.

Delivery is therefore at-least-once, not exactly-once. A relay that crashes after
publishing but before stamping `published_at` will publish again, so handlers
have to tolerate a repeat. That is the honest trade: losing an event is silent,
while handling one twice is something an idempotent handler absorbs.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutboxEventModel(Base):
    """One event awaiting publication, or one already published.

    A bigserial key rather than a UUID because this table is read in insertion
    order and nothing outside it holds a reference: ordering is the only identity
    an event needs, and a monotonic integer gives it for free.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        # The relay's only query is "what is still unpublished, oldest first", and
        # published rows quickly outnumber unpublished ones. A partial index keeps
        # that lookup proportional to the backlog rather than to the history.
        Index(
            "ix_outbox_events_unpublished",
            "id",
            postgresql_where="published_at IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # What the event is about — "document_revision", "profile_revision", "job".
    # Kept as a plain string rather than a foreign key: an event must outlive the
    # row it describes, or purging a vacancy would erase the record that it was
    # ever ingested.
    aggregate_type: Mapped[str]
    aggregate_id: Mapped[str]
    event_type: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(default=None)
    # Incremented by the relay when publishing raises. Persisted rather than kept
    # in memory so a permanently failing event is visible in the table instead of
    # being retried forever by successive workers, each starting from zero.
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(default=None)
