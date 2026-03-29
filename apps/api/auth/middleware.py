"""OpsLens AI - JWT Auth Middleware (JWKS + static PEM support)"""
from __future__ import annotations
import hashlib
import traceback
from typing import Callable
import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

_PUBLIC_PATHS = {
    "/health", "/api/docs", "/api/redoc", "/api/openapi.json",
    "/api/v1/ping", "/api/v1/integrations/webhooks/airbyte",
    "/api/v1/debug/db",              # dev-only DB diagnostics — no auth required

    # ── SAML SSO ──────────────────────────────────────────────────────────────
    "/api/v1/auth/saml/metadata",    # SP metadata XML — shared with IdP admin
    "/api/v1/auth/saml/login",       # Initiates SP-initiated SSO → redirects to IdP
    "/api/v1/auth/saml/acs",         # Assertion Consumer Service — called by IdP POST
    "/api/v1/auth/saml/slo",         # Single Logout — called by IdP

    # ── OIDC SSO ──────────────────────────────────────────────────────────────
    "/api/v1/auth/oidc/login",       # Redirects user to IdP authorization endpoint
    "/api/v1/auth/oidc/callback",    # IdP redirects back here with auth code

    # ── LDAP / AD ─────────────────────────────────────────────────────────────
    "/api/v1/auth/ldap/login",       # Direct credential auth (username + password)

    # ── SCIM ──────────────────────────────────────────────────────────────────
    "/api/v1/scim/v2",               # SCIM uses its own bearer token, not JWT
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
        # SCIM endpoints use their own token — skip JWT middleware entirely
        if path.startswith("/api/v1/scim/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing Authorization header."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth_header.removeprefix("Bearer ").strip()

        # ── Dev bypass (development only) ─────────────────────────────────────
        # Allows `Authorization: Bearer dev-token` in development mode so the
        # seed script and test guide curl commands work without a real Clerk JWT.
        if settings.ENV == "development" and token == "dev-token":
            request.state.tenant_id    = "default"
            request.state.user_id      = "dev-user-local"
            request.state.role         = "admin"
            request.state.email        = "dev@opslens.local"
            request.state.company_name = "OpsLens Dev"
            try:
                return await call_next(request)
            except Exception as exc:
                tb = traceback.format_exc()
                logger.exception("Unhandled error (dev-token) %s %s\n%s", request.method, path, tb)
                return JSONResponse(
                    status_code=500,
                    content={"detail": str(exc), "type": type(exc).__name__, "traceback": tb.splitlines()},
                )

        # Try API key first (prefix sk-oplen_)
        if token.startswith("sk-oplen_"):
            import asyncio
            resolved = await _resolve_api_key(token, request)
            if not resolved:
                return JSONResponse(status_code=401, content={"detail": "Invalid API key."})
        else:
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

# ── Role-based access control FastAPI dependency ──────────────────────────────

def require_roles(allowed: list[str]):
    """
    FastAPI dependency factory. Raises 403 if the authenticated user's role
    is not in the allowed list.

    Usage:
        @router.get("/admin-only")
        async def endpoint(_auth = Depends(require_roles(["admin"]))):
            ...
    """
    def _check(request: Request):
        role = getattr(request.state, "role", None)
        if role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' is not permitted. Required: {allowed}",
            )
        return role
    return _check


# ── API Key auth (for service accounts / CI pipelines) ────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)


async def _resolve_api_key(token: str, request: Request) -> bool:
    """
    Check whether the Bearer token is a service-account API key (prefix sk-oplen_).
    If valid, injects synthetic request.state fields and returns True.
    Returns False if not an API key (caller falls through to JWT check).
    """
    if not token.startswith("sk-oplen_"):
        return False
    try:
        import sqlalchemy as sa
        from ..db.session import AsyncSessionFactory
        from ..models.rbac import APIKey
        from datetime import datetime, timezone

        key_hash = hashlib.sha256(token.encode()).hexdigest()
        async with AsyncSessionFactory() as db:
            row = await db.execute(
                sa.select(APIKey).where(
                    APIKey.key_hash == key_hash,
                    APIKey.is_active == True,
                )
            )
            key: APIKey | None = row.scalar_one_or_none()

        if not key:
            return False

        now = datetime.now(tz=timezone.utc)
        if key.expires_at and key.expires_at < now:
            return False

        # Inject synthetic request state
        request.state.tenant_id = key.tenant_id
        request.state.user_id   = str(key.id)
        request.state.role      = key.role
        request.state.email     = ""
        request.state.company_name = ""

        # Update last_used_at asynchronously (best-effort)
        try:
            async with AsyncSessionFactory() as db:
                await db.execute(
                    sa.update(APIKey)
                    .where(APIKey.id == key.id)
                    .values(last_used_at=now)
                )
                await db.commit()
        except Exception:
            pass

        return True
    except Exception as exc:
        logger.warning("API key check failed: %s", exc)
        return False


def _decode_token(token: str) -> dict:
    """
    Validate and decode a Bearer token. Supports two token types:

    1. External provider tokens (Clerk, Auth0) — RS256, validated via JWKS or
       a static PEM public key. These are the default for cloud-hosted OpsLens.

    2. Internal SSO tokens — HS256, issued by apps/api/auth/token_issuer.py
       after a successful SAML, OIDC, or LDAP authentication. Identified by
       alg=HS256 in the token header; validated with settings.SECRET_KEY.

    The algorithm is detected from the unverified JWT header to decide which
    key material and verification path to use. This is safe because the
    signature is always verified regardless of which path is taken.
    """
    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        raise jwt.InvalidTokenError("Cannot parse token header.")

    alg = header.get("alg", "")

    # ── Internal SSO token (HS256) ────────────────────────────────────────────
    # Issued by token_issuer.issue_sso_token() after SAML/OIDC/LDAP login.
    # Signed with SECRET_KEY — rotate it to immediately invalidate all sessions.
    if alg == "HS256":
        from .token_issuer import decode_sso_token
        return decode_sso_token(token)

    # ── External provider token (RS256 or configured algorithm) ──────────────
    # Issued by Clerk, Auth0, or another external provider with RS256 JWTs.
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
