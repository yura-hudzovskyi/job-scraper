from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


_engine = create_async_engine(_async_database_url())
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
