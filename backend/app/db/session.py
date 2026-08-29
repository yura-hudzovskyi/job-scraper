from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import get_settings


def _async_database_url() -> str:
    """Settings.database_url uses the psycopg driver (what Alembic's sync engine
    needs) — swap it for asyncpg here. psycopg's async mode can't run under
    uvicorn's Windows event loop policy (ProactorEventLoop; uvicorn sets this itself
    on Windows, overriding anything set beforehand), and asyncpg has no such
    restriction. Doesn't affect Linux/Docker, where this bug doesn't exist either
    way — this only matters for native Windows dev.
    """
    url = get_settings().database_url
    return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)


def _build_engine() -> AsyncEngine:
    return create_async_engine(_async_database_url())


_engine = _build_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def reset_engine() -> None:
    """Recreates the engine/pool in-place. Celery's prefork pool imports this module
    once in the parent process, then forks worker children — each child inherits the
    parent's already-created asyncpg connections/pool, and using them from a
    different process's event loop corrupts the connection (asyncpg raises "cannot
    perform operation: another operation is in progress"). Call this from
    worker_process_init (see workers/celery_app.py) so each forked child gets its own
    fresh engine instead of the inherited one.
    """
    global _engine, _session_factory
    _engine = _build_engine()
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession]:
    """For use outside FastAPI's dependency injection — Celery tasks, scripts."""
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with session_scope() as session:
        yield session
