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

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .auth.middleware import JWTAuthMiddleware
from .config import settings
from .db.session import engine, Base
from .routers import alerts, ingestion, insights, rag, users
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

    # Dev/test: create tables directly. Production: use Alembic.
    if settings.ENV != "production":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified / created.")

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

    # ── Global exception handlers ─────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s", request.method, request.url)
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

    return app


app = create_app()
