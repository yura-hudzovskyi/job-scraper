"""ORM table for the AI call ledger — the durable half of
app/integrations/ai/quota/ledger.py.

Append-only and never read on a hot path: this exists so the daily limits in
Settings can be tuned against what actually happened (which capability burns the
quota, how often a leg fails and with what) instead of guesswork. Rows are pruned
by the flush task, since a call history older than a few weeks answers no
question this app asks.
"""

from datetime import datetime

from sqlalchemy import Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class AiInvocationModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_invocations"
    __table_args__ = (Index("ix_ai_invocations_created_at", "created_at"),)

    capability: Mapped[str]
    provider: Mapped[str]
    model: Mapped[str]
    # "ok", or the FailureKind that ended the attempt.
    outcome: Mapped[str]
    status: Mapped[int | None] = mapped_column(default=None)
    latency_ms: Mapped[int]
    # A vendor-neutral proxy for cost: providers report token usage in four
    # different shapes, and a length measured here is honest about being an
    # approximation.
    prompt_chars: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
