"""
OpsLens AI — Worker Database Session
======================================
Worker tasks each run in their own event loop (via run_async()).
Using the API's shared engine with a connection pool causes:

    RuntimeError: Future attached to a different loop

because asyncpg connections in the pool are bound to the event loop
that was active when they were first created. When a new Celery task
creates a fresh loop, those pooled connections are incompatible.

Fix: NullPool — no connection reuse across calls. Each async with
AsyncSession() opens a fresh connection inside the current loop and
closes it immediately on exit. Slightly more overhead per task, but
completely safe with Celery's per-task event loop pattern.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession as _AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool

from apps.api.config import settings


def _async_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# NullPool: never reuse connections — safe across multiple event loops
_engine = create_async_engine(
    _async_db_url(settings.DATABASE_URL),
    poolclass=NullPool,
    echo=False,
)

_SessionFactory = async_sessionmaker(
    _engine,
    class_=_AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@asynccontextmanager
async def AsyncSession():
    """
    Async context manager for Celery worker tasks.
    Creates a fresh DB connection for every call — no pool contamination.

    Usage:
        async with AsyncSession() as db:
            result = await db.execute(...)
    """
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
