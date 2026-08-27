"""Generic repository contract. Services depend on repository interfaces, never on
SQLAlchemy sessions directly, so persistence can be swapped or mocked in tests."""

from typing import Protocol, TypeVar

T = TypeVar("T")


class Repository(Protocol[T]):
    async def get(self, id: str) -> T | None: ...
    async def add(self, entity: T) -> T: ...
