from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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


# NullPool: every checkout opens a fresh asyncpg connection and closes it on
# checkin — no connection is ever held across calls. Required here because Celery
# tasks each wrap their async work in a fresh `asyncio.run(...)` (see
# workers/tasks/*.py), and a single worker process runs many tasks — i.e. many
# independent event loops — over its lifetime. asyncpg connections are bound to the
# event loop that created them; a normal pool would hand out a connection created
# under a since-closed loop to a later task's new loop, corrupting it
# ("cannot perform operation: another operation is in progress"). The same applies
# across Celery's prefork pool forking worker child processes. FastAPI's uvicorn
# process runs one long-lived event loop, so it loses real pooling benefit here,
# but at this app's traffic volume correctness matters far more than the per-request
# connection-setup cost.
_engine = create_async_engine(_async_database_url(), poolclass=NullPool)
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
