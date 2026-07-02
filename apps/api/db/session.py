"""
OpsLens AI — Async SQLAlchemy Session Factory
===============================================
Provides the async engine, session factory, and FastAPI dependency.

Multi-tenancy pattern
---------------------
Every query against a tenant-scoped model MUST include a tenant_id filter.
Use the `tenant_select()` helper instead of bare `sa.select()`:

    # ✅ Correct — tenant isolation guaranteed
    q = tenant_select(Incident, ctx.tenant_id)

    # ❌ Wrong — missing tenant filter risks cross-tenant data exposure
    q = sa.select(Incident)

`tenant_select()` is a thin wrapper around `sa.select(...).where(Model.tenant_id == tenant_id)`.
It does NOT replace SQL — it just makes the pattern explicit and searchable via code review.
"""
from __future__ import annotations

import ssl
import uuid
from contextlib import asynccontextmanager
from typing import Any, Type, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from ..config import settings

_M = TypeVar("_M")


# ── ORM Base ──────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


def _async_db_url(url: str) -> str:
    """Ensure asyncpg driver is used regardless of how Railway injects the URL."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _extract_sslmode(url: str) -> tuple[str, str | None]:
    """
    Strip a libpq-style `sslmode` query param off the URL and return it separately.

    asyncpg has no `sslmode` kwarg (only a plain `ssl` param), so a DATABASE_URL
    with `?sslmode=require` — which Postgres providers append by convention —
    gets passed straight through by SQLAlchemy's asyncpg dialect as an unknown
    keyword argument, raising `TypeError: connect() got an unexpected keyword
    argument 'sslmode'` before a connection is even attempted.
    """
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    sslmode = None
    remaining = []
    for key, value in pairs:
        if key.lower() == "sslmode":
            sslmode = value.lower()
        else:
            remaining.append((key, value))
    cleaned = urlunsplit(parts._replace(query=urlencode(remaining)))
    return cleaned, sslmode


def _ssl_connect_args(sslmode: str | None) -> dict[str, Any]:
    """Translate a libpq sslmode into asyncpg's `ssl` connect arg."""
    if sslmode in (None, "disable"):
        return {}
    ctx = ssl.create_default_context()
    if sslmode in ("require", "prefer", "allow"):
        # Encrypt, but don't demand a verifiable cert chain — Railway's managed
        # Postgres (and most managed providers) present certs that aren't in
        # the system trust store, so verify-full would reject every connection.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif sslmode == "verify-ca":
        ctx.check_hostname = False
    # verify-full: leave check_hostname/verify_mode at their secure defaults.
    return {"ssl": ctx}


_DB_URL, _DB_SSLMODE = _extract_sslmode(_async_db_url(settings.DATABASE_URL))

# ── Engine ────────────────────────────────────────────────────────────────────
engine: AsyncEngine = create_async_engine(
    _DB_URL,
    connect_args=_ssl_connect_args(_DB_SSLMODE),
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


# ── Tenant-scoped query helper (Q8 — multi-tenancy enforcement) ───────────────

def tenant_select(model: Type[_M], tenant_id: Any) -> sa.Select:
    """
    Return a SELECT statement pre-filtered to the given tenant.

    Usage:
        q = tenant_select(Incident, ctx.tenant_id)
        q = tenant_select(Incident, ctx.tenant_uuid)  # both str and UUID accepted

    Always use this instead of:
        sa.select(Incident).where(Incident.tenant_id == tenant_id)

    This makes tenant-scoped queries explicit, auditable, and grep-able.
    A missing tenant_id filter is a cross-tenant data leak — a critical compliance failure.
    """
    return sa.select(model).where(model.tenant_id == tenant_id)


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
