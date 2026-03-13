"""
OpsLens AI — JWT Authentication Middleware
============================================
Validates RS256 JWTs on every incoming request.
Extracts tenant_id, user_id, and role from claims and
injects them into request.state for downstream use.

Compatible with Clerk and Auth0 token formats.
"""
from __future__ import annotations

import time
from typing import Callable

import jwt
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

# Paths that bypass authentication
_PUBLIC_PATHS = {
    "/health",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
    "/api/v1/integrations/webhooks/airbyte",  # webhook — validated by shared secret
}


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that validates RS256 JWT tokens.

    On success:
        Sets request.state.tenant_id, .user_id, .role, .company_name

    On failure:
        Returns 401 Unauthorized with a descriptive error message.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Skip auth for public paths and OPTIONS preflight
        if request.method == "OPTIONS" or path in _PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing Authorization header. Expected: Bearer <token>"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.removeprefix("Bearer ").strip()

        try:
            payload = self._decode_token(token)
        except jwt.ExpiredSignatureError:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Token has expired. Please re-authenticate."},
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            )
        except jwt.InvalidTokenError as exc:
            logger.warning("JWT validation failed: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Invalid token: {exc}"},
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            )

        # Inject claims into request state
        request.state.tenant_id   = payload.get("tenant_id") or payload.get("org_id", "")
        request.state.user_id     = payload.get("sub", "")
        request.state.role        = payload.get("role", "viewer")
        request.state.company_name = payload.get("company_name", "your company")

        if not request.state.tenant_id:
            logger.warning("JWT missing tenant_id claim for user %s", request.state.user_id)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "JWT missing required 'tenant_id' claim."},
            )

        return await call_next(request)

    @staticmethod
    def _decode_token(token: str) -> dict:
        options = {"verify_exp": True}
        kwargs: dict = {
            "algorithms": [settings.JWT_ALGORITHM],
            "options": options,
        }
        if settings.JWT_AUDIENCE:
            kwargs["audience"] = settings.JWT_AUDIENCE
        if settings.JWT_ISSUER:
            kwargs["issuer"] = settings.JWT_ISSUER

        return jwt.decode(
            token,
            settings.JWT_PUBLIC_KEY,
            **kwargs,
        )
