"""Single-user shortcut: this is a personal tool with no auth, so every entry point
(API requests, Celery tasks) that needs "the user" resolves to one lazily-created
default account instead. Not framework-specific, so both api/deps.py and worker tasks
can share it.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import UserModel

DEFAULT_USER_EMAIL = "ygudzovski@gmail.com"


async def get_or_create_default_user_id(session: AsyncSession) -> uuid.UUID:
    result = await session.execute(select(UserModel).where(UserModel.email == DEFAULT_USER_EMAIL))
    user = result.scalar_one_or_none()
    if user is None:
        user = UserModel(email=DEFAULT_USER_EMAIL)
        session.add(user)
        await session.flush()
    return user.id
