"""ORM tables for CandidateProfile, CVDocument, CandidateSkill, UserPreference.

Column-level detail (JSON vs. normalized skill table, etc.) is deferred until the
matching engine's real query patterns are known — see docs/domain-model.md.
"""

import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CandidateProfileModel(Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]


class UserPreferenceModel(Base):
    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]
