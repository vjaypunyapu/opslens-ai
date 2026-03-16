"""
OpsLens AI — FastAPI Application Factory
=========================================
Entry point for the REST API. Registers all routers, middleware, and lifecycle hooks.

Run locally:
    uvicorn apps.api.main:app --reload --port 8080

Environment variables: see apps/api/config.py
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import sqlalchemy as sa
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .auth.middleware import JWTAuthMiddleware
from .config import settings
from .db.session import engine, Base
from .routers import alerts, dashboard, incidents, ingestion, insights, rag, settings as settings_router, users
from .utils.logging import configure_logging, get_logger

logger = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown lifecycle.
    In production, database tables are created via Alembic migrations (not here).
    """
    configure_logging()
    logger.info("OpsLens API starting up (env=%s)", settings.ENV)

    # Dev/test: ensure schema exists, then create tables. Production: use Alembic.
    if settings.ENV != "production":
        async with engine.begin() as conn:
            # Create schema first — create_all won't do this automatically
            await conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS opslens"))
            await conn.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
            await conn.run_sync(Base.metadata.create_all)
            # Ensure nullable columns that were created NOT NULL before the ORM was
            # updated are fixed.  ALTER COLUMN … DROP NOT NULL is idempotent.
            await conn.execute(sa.text(
                "ALTER TABLE opslens.chat_sessions "
                "ALTER COLUMN user_id DROP NOT NULL"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.chat_sessions "
                "ALTER COLUMN title DROP NOT NULL"
            ))
            # chat_messages columns added after initial create_all — idempotent
            await conn.execute(sa.text(
                "ALTER TABLE opslens.chat_messages "
                "ADD COLUMN IF NOT EXISTS sources JSONB NOT NULL DEFAULT '[]'"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.chat_messages "
                "ADD COLUMN IF NOT EXISTS token_count INTEGER"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.chat_messages "
                "ADD COLUMN IF NOT EXISTS latency_ms INTEGER"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.chat_messages "
                "ADD COLUMN IF NOT EXISTS feedback VARCHAR(20)"
            ))
            # insights columns added after initial create_all — idempotent
            await conn.execute(sa.text(
                "ALTER TABLE opslens.insights "
                "ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.insights "
                "ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.insights "
                "ADD COLUMN IF NOT EXISTS snoozed_until TIMESTAMPTZ"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.insights "
                "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ))
            # canonical_documents: columns added after initial create_all
            await conn.execute(sa.text(
                "ALTER TABLE opslens.canonical_documents "
                "ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.canonical_documents "
                "ADD COLUMN IF NOT EXISTS chunk_count INTEGER"
            ))
        logger.info("Database schema + tables verified / created.")

    yield

    await engine.dispose()
    logger.info("OpsLens API shut down cleanly.")


# ── App factory ───────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title="OpsLens AI",
        description=(
            "Operational Intelligence Copilot — REST API.\n\n"
            "All endpoints require `Authorization: Bearer <JWT>` (RS256).\n"
            "JWT claims must include `tenant_id`, `user_id`, and `role`."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs"   if settings.ENV != "production" else None,
        redoc_url="/api/redoc" if settings.ENV != "production" else None,
        openapi_url="/api/openapi.json" if settings.ENV != "production" else None,
    )

    # ── CORS ─────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ── Auth (runs after CORS) ────────────────────────────────────────────────
    app.add_middleware(JWTAuthMiddleware)

    # ── Routers ───────────────────────────────────────────────────────────────
    # Each router is mounted under its own prefix.
    # Tags appear in the auto-generated Swagger docs.

    app.include_router(
        dashboard.router,
        prefix="/api/v1",
        tags=["Dashboard"],
    )
    app.include_router(
        rag.router,
        prefix="/api/v1/chat",
        tags=["Chat / RAG"],
    )
    app.include_router(
        insights.router,
        prefix="/api/v1/insights",
        tags=["Insights"],
    )
    app.include_router(
        alerts.router,
        prefix="/api/v1/alerts",
        tags=["Alerts"],
    )
    app.include_router(
        ingestion.router,
        prefix="/api/v1/integrations",
        tags=["Integrations / Ingestion"],
    )
    app.include_router(
        users.router,
        prefix="/api/v1",
        tags=["Users & Tenant"],
    )
    app.include_router(
        settings_router.router,
        prefix="/api/v1",
        tags=["Settings"],
    )
    app.include_router(
        incidents.router,
        prefix="/api/v1/incidents",
        tags=["Incidents"],
    )

    # ── Global exception handlers ─────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import traceback
        tb = traceback.format_exc()
        logger.exception("Unhandled exception on %s %s\n%s", request.method, request.url, tb)
        # In development, surface the real error so it's visible in the browser
        if settings.ENV == "development":
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": str(exc),
                    "type": type(exc).__name__,
                    "traceback": tb.splitlines(),
                },
            )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error. Please try again."},
        )

    # ── Utility endpoints ─────────────────────────────────────────────────────
    @app.get("/health", tags=["Ops"], include_in_schema=False)
    async def health_check():
        return {"status": "ok", "version": "1.0.0", "env": settings.ENV}

    @app.get("/api/v1/ping", tags=["Ops"])
    async def ping():
        """Lightweight authenticated ping — verifies JWT middleware is working."""
        return {"pong": True}

    @app.get("/api/v1/debug/db", tags=["Ops"], include_in_schema=False)
    async def debug_db():
        """
        DEV-ONLY: test the full tenant-provision + chat-session DB round-trip.
        No auth required. Remove before deploying to production.
        """
        import traceback as _tb
        import uuid as _uuid
        import re as _re
        import sqlalchemy as _sa
        from .db.session import AsyncSessionFactory
        from .db.models import Tenant, ChatSession

        results: dict = {}

        async with AsyncSessionFactory() as db:
            try:
                # ── 1. schema existence check ──────────────────────────────
                r = await db.execute(_sa.text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name = 'opslens'"
                ))
                results["opslens_schema_exists"] = r.scalar_one_or_none() == "opslens"

                # ── 2. tables existence check ──────────────────────────────
                for tbl in ("tenants", "chat_sessions", "insights", "alert_rules"):
                    r2 = await db.execute(_sa.text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname='opslens' AND tablename=:t"
                    ), {"t": tbl})
                    results[f"table_{tbl}"] = r2.scalar_one_or_none() == tbl

                # ── 3. simulate _provision_tenant ─────────────────────────
                tenant_id_str = "http://localhost:3000"
                try:
                    tuuid_str = str(_uuid.UUID(tenant_id_str))
                except ValueError:
                    tuuid_str = str(_uuid.uuid5(_uuid.NAMESPACE_URL, tenant_id_str))
                tuuid_obj = _uuid.UUID(tuuid_str)

                r3 = await db.execute(
                    _sa.select(Tenant.id).where(Tenant.id == tuuid_obj)
                )
                tenant_exists = r3.scalar_one_or_none() is not None
                results["tenant_exists"] = tenant_exists
                results["tenant_uuid"] = tuuid_str

                if not tenant_exists:
                    safe_slug = _re.sub(r"[^a-z0-9]+", "-", tenant_id_str.lower()).strip("-")[:99] or "workspace"
                    tenant = Tenant(id=tuuid_obj, name="Debug Workspace", slug=safe_slug, plan="starter", settings={})
                    db.add(tenant)
                    try:
                        await db.commit()
                        results["tenant_created"] = True
                    except Exception as e:
                        await db.rollback()
                        results["tenant_create_error"] = str(e)
                        results["tenant_create_error_type"] = type(e).__name__
                        results["tenant_create_traceback"] = _tb.format_exc().splitlines()
                        return results

                # ── 4. simulate create_session ────────────────────────────
                session = ChatSession(tenant_id=tuuid_obj, user_id=None)
                db.add(session)
                try:
                    await db.commit()
                    await db.refresh(session)
                    results["session_created"] = True
                    results["session_id"] = str(session.id)
                    # clean up
                    await db.delete(session)
                    await db.commit()
                    results["session_cleaned_up"] = True
                except Exception as e:
                    await db.rollback()
                    results["session_create_error"] = str(e)
                    results["session_create_error_type"] = type(e).__name__
                    results["session_create_traceback"] = _tb.format_exc().splitlines()

            except Exception as e:
                results["unexpected_error"] = str(e)
                results["unexpected_error_type"] = type(e).__name__
                results["unexpected_traceback"] = _tb.format_exc().splitlines()

        return results

    return app


app = create_app()
