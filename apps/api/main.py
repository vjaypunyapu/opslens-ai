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
from .routers import (
    admin, alerts, billing, dashboard, enterprise, incidents, ingestion, insights,
    log_ops, manager_dashboard, rag, retention, rrt_briefs,
    settings as settings_router, timeline, users,
)
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
            # insights: schema was redesigned — original SQL used TEXT/NUMERIC types
            # that conflict with current ORM. Fix column types idempotently.

            # confidence: was TEXT ('high'/'medium'/'low') → NUMERIC(3,2) float 0-1
            # Uses CASCADE to drop any dependent views (e.g. v_active_insights)
            await conn.execute(sa.text("""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'opslens' AND table_name = 'insights'
                          AND column_name = 'confidence' AND data_type != 'numeric'
                    ) THEN
                        ALTER TABLE opslens.insights DROP COLUMN confidence CASCADE;
                        ALTER TABLE opslens.insights ADD COLUMN confidence NUMERIC(3,2);
                    END IF;
                END; $$
            """))

            # magnitude: was NUMERIC → VARCHAR(20) label ('critical'/'high'/'medium'/'low')
            # Uses CASCADE to drop any dependent views
            await conn.execute(sa.text("""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'opslens' AND table_name = 'insights'
                          AND column_name = 'magnitude' AND data_type = 'numeric'
                    ) THEN
                        ALTER TABLE opslens.insights DROP COLUMN magnitude CASCADE;
                        ALTER TABLE opslens.insights
                            ADD COLUMN magnitude VARCHAR(20) NOT NULL DEFAULT 'medium';
                    END IF;
                END; $$
            """))

            # source_types: was TEXT[] → JSONB array
            # Must drop the old default before changing type, then set new JSONB default
            await conn.execute(sa.text("""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'opslens' AND table_name = 'insights'
                          AND column_name = 'source_types' AND data_type = 'ARRAY'
                    ) THEN
                        ALTER TABLE opslens.insights
                            ALTER COLUMN source_types DROP DEFAULT;
                        ALTER TABLE opslens.insights
                            ALTER COLUMN source_types TYPE JSONB
                            USING to_jsonb(source_types);
                        ALTER TABLE opslens.insights
                            ALTER COLUMN source_types SET NOT NULL;
                        ALTER TABLE opslens.insights
                            ALTER COLUMN source_types SET DEFAULT '[]'::jsonb;
                    END IF;
                END; $$
            """))

            # ADD any missing columns (all idempotent)
            await conn.execute(sa.text(
                "ALTER TABLE opslens.insights "
                "ADD COLUMN IF NOT EXISTS source_types JSONB NOT NULL DEFAULT '[]'"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.insights "
                "ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '{}'"
            ))
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
                "ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.insights "
                "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ))
            # users: updated_at was added to ORM after initial schema
            await conn.execute(sa.text(
                "ALTER TABLE opslens.users "
                "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ))

            # integrations: original schema used airbyte_conn_id; ORM uses longer names
            await conn.execute(sa.text(
                "ALTER TABLE opslens.integrations "
                "ADD COLUMN IF NOT EXISTS airbyte_connection_id VARCHAR(255)"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.integrations "
                "ADD COLUMN IF NOT EXISTS airbyte_source_id VARCHAR(255)"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.integrations "
                "ADD COLUMN IF NOT EXISTS config JSONB"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.integrations "
                "ADD COLUMN IF NOT EXISTS total_records BIGINT NOT NULL DEFAULT 0"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.integrations "
                "ADD COLUMN IF NOT EXISTS error_message TEXT"
            ))

            # alert_rules: schema redesigned — original used different names/types
            await conn.execute(sa.text(
                "ALTER TABLE opslens.alert_rules "
                "ADD COLUMN IF NOT EXISTS description TEXT"
            ))
            # conditions (plural JSONB) — original had 'condition' (singular)
            await conn.execute(sa.text(
                "ALTER TABLE opslens.alert_rules "
                "ADD COLUMN IF NOT EXISTS conditions JSONB NOT NULL DEFAULT '[]'"
            ))
            # is_active — original had 'enabled'
            await conn.execute(sa.text(
                "ALTER TABLE opslens.alert_rules "
                "ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"
            ))
            # cooldown_minutes — original had cooldown_hours
            await conn.execute(sa.text(
                "ALTER TABLE opslens.alert_rules "
                "ADD COLUMN IF NOT EXISTS cooldown_minutes INTEGER NOT NULL DEFAULT 60"
            ))
            # last_triggered_at — original had last_fired_at
            await conn.execute(sa.text(
                "ALTER TABLE opslens.alert_rules "
                "ADD COLUMN IF NOT EXISTS last_triggered_at TIMESTAMPTZ"
            ))
            # channels: was TEXT[] → JSONB
            await conn.execute(sa.text("""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'opslens' AND table_name = 'alert_rules'
                          AND column_name = 'channels' AND data_type = 'ARRAY'
                    ) THEN
                        ALTER TABLE opslens.alert_rules
                            ALTER COLUMN channels DROP DEFAULT;
                        ALTER TABLE opslens.alert_rules
                            ALTER COLUMN channels TYPE JSONB
                            USING to_jsonb(channels);
                        ALTER TABLE opslens.alert_rules
                            ALTER COLUMN channels SET NOT NULL;
                        ALTER TABLE opslens.alert_rules
                            ALTER COLUMN channels SET DEFAULT '[]'::jsonb;
                    END IF;
                END; $$
            """))

            # alert_history: column names changed between schema versions
            await conn.execute(sa.text(
                "ALTER TABLE opslens.alert_history "
                "ADD COLUMN IF NOT EXISTS trigger_data JSONB NOT NULL DEFAULT '{}'"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.alert_history "
                "ADD COLUMN IF NOT EXISTS channels_notified JSONB NOT NULL DEFAULT '[]'"
            ))
            await conn.execute(sa.text(
                "ALTER TABLE opslens.alert_history "
                "ADD COLUMN IF NOT EXISTS triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
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
            await conn.execute(sa.text(
                "ALTER TABLE opslens.canonical_documents "
                "ADD COLUMN IF NOT EXISTS source_id VARCHAR(512)"
            ))

            # ── RBAC: Teams, TeamMembers, TeamResourcePermissions, PendingInvites ──
            # These are created by create_all() above but may not exist on older
            # deployments — the ADD COLUMN IF NOT EXISTS statements below are
            # idempotent guards for rolling upgrades.
            await conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS opslens.teams (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id UUID NOT NULL REFERENCES opslens.tenants(id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    created_by VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            await conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS opslens.team_members (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    team_id UUID NOT NULL REFERENCES opslens.teams(id) ON DELETE CASCADE,
                    user_external_id VARCHAR(255) NOT NULL,
                    user_email VARCHAR(255),
                    role VARCHAR(50) NOT NULL DEFAULT 'member',
                    added_by VARCHAR(255),
                    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_team_members_user UNIQUE (team_id, user_external_id)
                )
            """))
            await conn.execute(sa.text(
                "CREATE INDEX IF NOT EXISTS ix_team_members_user_external_id "
                "ON opslens.team_members (user_external_id)"
            ))
            await conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS opslens.team_resource_permissions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    team_id UUID NOT NULL REFERENCES opslens.teams(id) ON DELETE CASCADE,
                    source_type VARCHAR(50) NOT NULL,
                    source_id VARCHAR(512) NOT NULL,
                    can_read BOOLEAN NOT NULL DEFAULT TRUE,
                    can_see_metrics BOOLEAN NOT NULL DEFAULT FALSE,
                    can_see_logs BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_team_resource UNIQUE (team_id, source_type, source_id)
                )
            """))
            await conn.execute(sa.text("""
                CREATE TABLE IF NOT EXISTS opslens.pending_invites (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id UUID NOT NULL REFERENCES opslens.tenants(id) ON DELETE CASCADE,
                    team_id UUID REFERENCES opslens.teams(id) ON DELETE SET NULL,
                    email VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'member',
                    team_role VARCHAR(50) NOT NULL DEFAULT 'member',
                    invited_by VARCHAR(255),
                    accepted_at TIMESTAMPTZ,
                    expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            await conn.execute(sa.text(
                "CREATE INDEX IF NOT EXISTS ix_pending_invites_email "
                "ON opslens.pending_invites (email)"
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
        allow_origins=[o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()],
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
        admin.router,
        prefix="/api/v1/admin",
        tags=["Admin — Teams & RBAC"],
    )
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
    app.include_router(
        log_ops.router,
        prefix="/api/v1/log-ops",
        tags=["Log Ops — Known Issues & Team Routing"],
    )
    app.include_router(
        rrt_briefs.router,
        prefix="/api/v1/rrt-briefs",
        tags=["RRT Briefs — Incident Artifacts"],
    )
    app.include_router(
        timeline.router,
        prefix="/api/v1/timeline",
        tags=["Delivery Risk Timeline"],
    )
    app.include_router(
        manager_dashboard.router,
        prefix="/api/v1/manager",
        tags=["Manager Dashboard"],
    )
    app.include_router(
        enterprise.router,
        prefix="/api/v1",
        tags=["Enterprise — SAML SSO", "Enterprise — SCIM",
              "Enterprise — RBAC", "Enterprise — Audit Log"],
    )
    app.include_router(
        retention.router,
        prefix="/api/v1/retention",
        tags=["Retention Controls"],
    )
    app.include_router(
        billing.router,
        prefix="/api/v1/billing",
        tags=["Billing & Metering"],
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
        """
        Deep dependency health check. Returns 200 if all critical services are reachable,
        503 if Postgres or Redis is down.

        Critical:  postgres, redis
        Degraded:  qdrant (RAG unavailable), celery_broker (background tasks down)
        """
        import time
        import httpx as _httpx

        checks: dict[str, dict] = {}
        overall_ok = True

        # ── PostgreSQL ────────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            from .db.session import AsyncSessionFactory
            async with AsyncSessionFactory() as db:
                await db.execute(sa.text("SELECT 1"))
            checks["postgres"] = {
                "status": "ok",
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            }
        except Exception as exc:
            checks["postgres"] = {"status": "error", "detail": str(exc)[:120]}
            overall_ok = False

        # ── Redis ─────────────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
            await r.ping()
            await r.aclose()
            checks["redis"] = {
                "status": "ok",
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            }
        except Exception as exc:
            checks["redis"] = {"status": "error", "detail": str(exc)[:120]}
            overall_ok = False

        # ── Qdrant ────────────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            resp = _httpx.get(f"{settings.QDRANT_URL}/healthz", timeout=3)
            checks["qdrant"] = {
                "status": "ok" if resp.status_code == 200 else "degraded",
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            }
        except Exception as exc:
            checks["qdrant"] = {
                "status": "degraded",
                "detail": str(exc)[:120],
                "note": "RAG enrichment unavailable — alert detection still works",
            }

        # ── Celery broker ─────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            import redis.asyncio as aioredis
            rb = aioredis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=3)
            await rb.ping()
            await rb.aclose()
            checks["celery_broker"] = {
                "status": "ok",
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            }
        except Exception as exc:
            checks["celery_broker"] = {
                "status": "degraded",
                "detail": str(exc)[:120],
                "note": "Background tasks unavailable — fast alerts and RRT briefs will not fire",
            }

        # ── LLM provider ──────────────────────────────────────────────────────
        if settings.LLM_PROVIDER == "ollama":
            t0 = time.monotonic()
            try:
                resp = _httpx.get(f"{settings.OLLAMA_URL}/api/version", timeout=3)
                checks["llm"] = {
                    "provider": "ollama",
                    "status": "ok" if resp.status_code == 200 else "degraded",
                    "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                }
            except Exception as exc:
                checks["llm"] = {"provider": "ollama", "status": "degraded", "detail": str(exc)[:80]}
        elif settings.LLM_PROVIDER == "openai":
            checks["llm"] = {
                "provider": "openai",
                "status": "ok" if settings.OPENAI_API_KEY else "misconfigured",
                "key_configured": bool(settings.OPENAI_API_KEY),
            }
        elif settings.LLM_PROVIDER == "claude":
            checks["llm"] = {
                "provider": "claude",
                "status": "ok" if settings.ANTHROPIC_API_KEY else "misconfigured",
                "key_configured": bool(settings.ANTHROPIC_API_KEY),
            }

        http_status = 200 if overall_ok else 503
        return JSONResponse(
            status_code=http_status,
            content={
                "status": "ok" if overall_ok else "degraded",
                "version": "1.0.0",
                "env": settings.ENV,
                "notification_provider": settings.NOTIFICATION_PROVIDER,
                "llm_provider": settings.LLM_PROVIDER,
                "checks": checks,
            },
        )

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
