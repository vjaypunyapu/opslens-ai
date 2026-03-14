"""
OpsLens AI — Async SQLAlchemy Session Factory
===============================================
Provides the async engine, session factory, and FastAPI dependency.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from ..config import settings


# ── ORM Base ──────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


# ── Engine ────────────────────────────────────────────────────────────────────
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,     # reconnect if connection is stale
    echo=settings.ENV == "development",
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=_AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── Context manager (for workers / scripts) ───────────────────────────────────
@asynccontextmanager
async def AsyncSession():
    """
    Async context manager for use outside FastAPI request context.
    Handles commit/rollback automatically.

    Usage:
        async with AsyncSession() as db:
            result = await db.execute(...)
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── FastAPI Depends() ─────────────────────────────────────────────────────────
async def get_db() -> _AsyncSession:
    """
    FastAPI dependency that provides a per-request async database session.

    Usage:
        async def my_endpoint(db = Depends(get_db)): ...
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
