import uuid
from datetime import UTC, datetime

import pytest

from app.domain.auth.models import User
from app.services.auth_service import AuthService, EmailAlreadyRegistered, InvalidCredentials


class _FakeUserRepository:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    async def get_by_email(self, email: str) -> User | None:
        return self._users.get(email)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return next((u for u in self._users.values() if u.id == str(user_id)), None)

    async def create(self, email: str, password_hash: str) -> User:
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=password_hash,
            created_at=datetime.now(UTC),
        )
        self._users[email] = user
        return user


@pytest.mark.asyncio
async def test_register_creates_a_user_and_returns_a_token() -> None:
    service = AuthService(_FakeUserRepository(), "test-secret")  # type: ignore[arg-type]

    user, token = await service.register("Friend@Example.com", "hunter2hunter2")

    assert user.email == "friend@example.com"
    assert token


@pytest.mark.asyncio
async def test_register_rejects_a_duplicate_email() -> None:
    repository = _FakeUserRepository()
    service = AuthService(repository, "test-secret")  # type: ignore[arg-type]
    await service.register("friend@example.com", "hunter2hunter2")

    with pytest.raises(EmailAlreadyRegistered):
        await service.register("Friend@example.com", "different-password")


@pytest.mark.asyncio
async def test_login_succeeds_with_the_correct_password() -> None:
    repository = _FakeUserRepository()
    service = AuthService(repository, "test-secret")  # type: ignore[arg-type]
    await service.register("friend@example.com", "hunter2hunter2")

    user, token = await service.login("friend@example.com", "hunter2hunter2")

    assert user.email == "friend@example.com"
    assert token


@pytest.mark.asyncio
async def test_login_rejects_the_wrong_password() -> None:
    repository = _FakeUserRepository()
    service = AuthService(repository, "test-secret")  # type: ignore[arg-type]
    await service.register("friend@example.com", "hunter2hunter2")

    with pytest.raises(InvalidCredentials):
        await service.login("friend@example.com", "wrong-password")


@pytest.mark.asyncio
async def test_login_rejects_an_unknown_email() -> None:
    service = AuthService(_FakeUserRepository(), "test-secret")  # type: ignore[arg-type]

    with pytest.raises(InvalidCredentials):
        await service.login("nobody@example.com", "hunter2hunter2")
