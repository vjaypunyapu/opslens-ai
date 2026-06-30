"""Shared pytest fixtures for OpsLens AI test suite."""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.auth.dependencies import TenantContext
from apps.api.db.models import Base
from apps.api.db.session import get_db
from apps.api.main import app

# ─── Constants ────────────────────────────────────────────────────────────────
TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID   = uuid.UUID("00000000-0000-0000-0000-000000000002")
TEST_SESSION_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")

# ─── Test database ────────────────────────────────────────────────────────────
# Several models use Postgres-only column types (JSONB), so the test DB must
# be real Postgres -- a SQLite in-memory engine can't compile those columns.
# Point this at a throwaway DB (CI provisions one via the `postgres` service
# container in .github/workflows/ci.yml and loads apps/api/db/schema.sql).
TEST_DB_URL = (
    os.environ.get("TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql+asyncpg://opslens:opslens@localhost:5432/opslens_test"
)

_engine = create_async_engine(TEST_DB_URL, echo=False)
_TestingSessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_test_tables():
    """Create all ORM tables in the test DB once per test session.

    Teardown drops the whole `opslens` schema with CASCADE rather than
    Base.metadata.drop_all(): schema.sql also creates views (e.g.
    v_token_usage) that SQLAlchemy's metadata doesn't know about, and
    drop_all() fails with DependentObjectsStillExistError without CASCADE.
    """
    import sqlalchemy as sa

    async with _engine.begin() as conn:
        await conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS opslens"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA IF EXISTS opslens CASCADE"))


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional DB session that rolls back after each test."""
    async with _engine.begin() as conn, _TestingSessionLocal(bind=conn) as session:
        yield session
        await session.rollback()


# ─── FastAPI Test Client ───────────────────────────────────────────────────────
@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        role="admin",
        company_name="Acme Corp",
    )


@pytest_asyncio.fixture
async def api_client(db_session: AsyncSession, tenant_ctx: TenantContext) -> AsyncGenerator:
    """HTTP test client with DB and auth overrides applied."""

    async def override_get_db():
        yield db_session

    def override_require_viewer():
        return tenant_ctx

    def override_require_member():
        return tenant_ctx

    def override_require_admin():
        return tenant_ctx

    from apps.api.auth.dependencies import require_admin, require_member, require_viewer

    app.dependency_overrides[get_db]            = override_get_db
    app.dependency_overrides[require_viewer]    = override_require_viewer
    app.dependency_overrides[require_member]    = override_require_member
    app.dependency_overrides[require_admin]     = override_require_admin

    async with AsyncClient(app=app, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


# ─── Mock OpenAI ──────────────────────────────────────────────────────────────
@pytest.fixture
def mock_openai_embed():
    """Return deterministic 1536-dim embedding for any input."""
    with patch("openai.AsyncOpenAI") as mock:
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            return_value=MagicMock(
                data=[MagicMock(embedding=[0.1] * 1536)]
            )
        )
        mock.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_openai_chat():
    """Stream a fixed response token by token."""

    async def _fake_stream(*args, **kwargs):
        tokens = ["This ", "is ", "a ", "test ", "response."]
        for t in tokens:
            chunk = MagicMock()
            chunk.choices = [MagicMock(delta=MagicMock(content=t))]
            yield chunk

    with patch("langchain_openai.ChatOpenAI") as mock:
        instance = MagicMock()
        instance.astream = _fake_stream
        mock.return_value = instance
        yield instance


# ─── Mock Qdrant ──────────────────────────────────────────────────────────────
@pytest.fixture
def mock_qdrant():
    """Return empty search results from Qdrant by default."""
    with patch("qdrant_client.AsyncQdrantClient") as mock:
        instance = AsyncMock()
        instance.search = AsyncMock(return_value=[])
        instance.upsert = AsyncMock(return_value=MagicMock(status="completed"))
        instance.collection_exists = AsyncMock(return_value=True)
        mock.return_value = instance
        yield instance


# ─── Mock Redis / Celery ──────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def mock_celery_always_eager(monkeypatch):
    """Run Celery tasks synchronously (no broker needed in tests)."""
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setenv("CELERY_TASK_EAGER_PROPAGATES", "true")


# ─── Factory helpers ──────────────────────────────────────────────────────────
def make_canonical_doc(**kwargs) -> MagicMock:
    """Build a MagicMock CanonicalDocument for use in detector tests."""
    doc = MagicMock()
    doc.id           = kwargs.get("id", uuid.uuid4())
    doc.tenant_id    = kwargs.get("tenant_id", TEST_TENANT_ID)
    doc.source_type  = kwargs.get("source_type", "zendesk")
    doc.content      = kwargs.get("content", "Sample content")
    doc.title        = kwargs.get("title", "Sample Title")
    doc.author       = kwargs.get("author", "user@example.com")
    doc.url          = kwargs.get("url", "https://example.com/doc/1")
    doc.doc_metadata = kwargs.get("doc_metadata", {})
    doc.source_created_at = kwargs.get(
        "source_created_at", datetime.now(UTC)
    )
    return doc
