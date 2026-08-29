"""Use case: register/login. Password strength is enforced by the request schema
(routes/auth.py), not here. See docs/domain-model.md and app/security/.
"""

import uuid

from app.domain.auth.models import User
from app.repositories.user_repository import UserRepository
from app.security.passwords import hash_password, verify_password
from app.security.tokens import create_access_token


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class AuthService:
    def __init__(self, user_repository: UserRepository, secret_key: str):
        self._user_repository = user_repository
        self._secret_key = secret_key

    async def register(self, email: str, password: str) -> tuple[User, str]:
        email = email.strip().lower()
        if await self._user_repository.get_by_email(email) is not None:
            raise EmailAlreadyRegistered(email)

        user = await self._user_repository.create(email, hash_password(password))
        return user, create_access_token(uuid.UUID(user.id), self._secret_key)

    async def login(self, email: str, password: str) -> tuple[User, str]:
        email = email.strip().lower()
        user = await self._user_repository.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentials()

        return user, create_access_token(uuid.UUID(user.id), self._secret_key)

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self._user_repository.get_by_id(user_id)
