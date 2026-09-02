"""Generic repository contract. Services depend on repository interfaces, never on
SQLAlchemy sessions directly, so persistence can be swapped or mocked in tests."""

from typing import Any, Protocol, TypeVar, cast

from sqlalchemy import CursorResult
from sqlalchemy.engine import Result

T = TypeVar("T")


class Repository(Protocol[T]):
    async def get(self, id: str) -> T | None: ...
    async def add(self, entity: T) -> T: ...


def rows_affected(result: Result[Any]) -> int:
    """How many rows a DELETE/UPDATE touched.

    `session.execute()` is typed as returning `Result`, which has no `rowcount` —
    only the `CursorResult` a DML statement actually returns does. One narrowing
    here beats a `cast` at each of the dozen call sites that report what a reset
    deleted.
    """
    return int(cast("CursorResult[Any]", result).rowcount or 0)
