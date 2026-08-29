"""Persistence for accounts. See app/services/auth_service.py for registration/login."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import UserModel
from app.domain.auth.models import User


def _to_user(model: UserModel) -> User:
    return User(
        id=str(model.id),
        email=model.email,
        password_hash=model.password_hash,
        created_at=model.created_at,
    )


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(UserModel).where(UserModel.email == email))
        model = result.scalar_one_or_none()
        return _to_user(model) if model else None

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(UserModel).where(UserModel.id == user_id))
        model = result.scalar_one_or_none()
        return _to_user(model) if model else None

    async def create(self, email: str, password_hash: str) -> User:
        model = UserModel(email=email, password_hash=password_hash)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_user(model)
