"""OpsLens AI - JWT Auth Middleware (JWKS + static PEM support)"""
from __future__ import annotations

import traceback
from collections.abc import Callable

import jwt
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

_PUBLIC_PATHS = {
    "/health", "/api/docs", "/api/redoc", "/api/openapi.json",
    "/api/v1/ping", "/api/v1/integrations/webhooks/airbyte",
    "/api/v1/debug/db",  # dev-only DB diagnostics — no auth required
}

_jwks_client: PyJWKClient | None = None

def _get_jwks_client() -> PyJWKClient | None:
    global _jwks_client
    if settings.JWT_PUBLIC_KEY_URL and _jwks_client is None:
        _jwks_client = PyJWKClient(settings.JWT_PUBLIC_KEY_URL, cache_keys=True, lifespan=3600)
        logger.info("JWKS client initialised from %s", settings.JWT_PUBLIC_KEY_URL)
    return _jwks_client

class JWTAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or path in _PUBLIC_PATHS:
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing Authorization header."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth_header.removeprefix("Bearer ").strip()
        try:
            payload = _decode_token(token)
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Token expired."})
        except jwt.InvalidTokenError as exc:
            logger.warning("JWT validation failed: %s", exc)
            return JSONResponse(status_code=401, content={"detail": f"Invalid token: {exc}"})

        user_id = payload.get("sub", "")
        tenant_id = (payload.get("tenant_id") or payload.get("org_id")
                     or payload.get("azp", "") or user_id)
        request.state.tenant_id    = tenant_id
        request.state.user_id      = user_id
        request.state.role         = payload.get("role", "member")
        request.state.email        = payload.get("email", "")
        request.state.company_name = payload.get("company_name", "")
        if not request.state.tenant_id:
            return JSONResponse(status_code=401, content={"detail": "JWT missing tenant identifier."})

        # BaseHTTPMiddleware swallows exceptions from route handlers and returns
        # a raw HTML 500, bypassing FastAPI's exception handler. Catch explicitly
        # so we always return structured JSON and expose the real error in dev.
        try:
            return await call_next(request)
        except Exception as exc:
            tb = traceback.format_exc()
            logger.exception("Unhandled error during request %s %s\n%s", request.method, path, tb)
            if settings.ENV == "development":
                return JSONResponse(
                    status_code=500,
                    content={"detail": str(exc), "type": type(exc).__name__, "traceback": tb.splitlines()},
                )
            return JSONResponse(status_code=500, content={"detail": "Internal server error."})

def _decode_token(token: str) -> dict:
    kwargs: dict = {"algorithms": [settings.JWT_ALGORITHM], "options": {"verify_exp": True}}
    if settings.JWT_AUDIENCE:
        kwargs["audience"] = settings.JWT_AUDIENCE
    if settings.JWT_ISSUER:
        kwargs["issuer"] = settings.JWT_ISSUER
    jwks = _get_jwks_client()
    if jwks:
        return jwt.decode(token, jwks.get_signing_key_from_jwt(token).key, **kwargs)
    if settings.JWT_PUBLIC_KEY:
        return jwt.decode(token, settings.JWT_PUBLIC_KEY, **kwargs)
    logger.warning("No JWT key configured — skipping signature verification (dev only).")
    return jwt.decode(token, options={"verify_signature": False})
