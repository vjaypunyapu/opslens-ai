"""
OpsLens AI — Internal JWT Token Issuer
========================================
Issues short-lived HS256 JWTs for users authenticated via SAML, OIDC, or LDAP.

These "internal" tokens carry iss="opslens-internal" and are validated by the
JWT middleware alongside external Clerk/Auth0 RS256 tokens. The middleware
routes them through HS256 verification using settings.SECRET_KEY.

Token claims (match what the JWT middleware puts on request.state):
    sub         — user's external_id (or email when no Clerk ID exists)
    email       — user's email address
    tenant_id   — tenant UUID string
    role        — OpsLens role: "admin" | "member" | "viewer"
    iss         — "opslens-internal" (distinguishes from external provider tokens)
    iat         — issued-at (Unix timestamp)
    exp         — expiry (Unix timestamp, default: 1 hour from issuance)

Security considerations:
  • Signed with settings.SECRET_KEY (HMAC-SHA256). Rotate SECRET_KEY to
    invalidate all outstanding internal tokens immediately.
  • Short-lived by design (1 hour). SSO sessions should re-authenticate via the
    IdP (SAML/OIDC) to refresh; they do not support silent renewal.
  • Token lifetime is configurable via INTERNAL_TOKEN_TTL_SECONDS env var.
  • The raw token is only transmitted over TLS (enforced at the infra layer).

Frontend integration:
  After a successful SSO callback, OpsLens redirects to:
      {FRONTEND_URL}/auth/sso-callback?token=<jwt>
  The frontend should extract the token, store it in memory (or sessionStorage),
  and send it as "Authorization: Bearer <jwt>" on subsequent API calls.
  Do NOT store SSO tokens in localStorage due to XSS risk.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from ..config import settings

# How long an internally-issued SSO token remains valid.
# Configured via INTERNAL_TOKEN_TTL_SECONDS env var (see apps/api/config.py).
# Default: 3600 seconds (1 hour).
_TTL_SECONDS: int = settings.INTERNAL_TOKEN_TTL_SECONDS

_ISSUER = "opslens-internal"
_ALGORITHM = "HS256"


def issue_sso_token(
    *,
    user_id: str,
    email: str,
    tenant_id: str,
    role: str,
    ttl_seconds: int | None = None,
) -> str:
    """
    Generate a signed HS256 JWT for a user authenticated via SAML, OIDC, or LDAP.

    Args:
        user_id:    Stable user identifier. Use Clerk external_id when available,
                    otherwise use the user's email (SAML NameID / LDAP DN hash).
        email:      User's email address.
        tenant_id:  Tenant UUID string.
        role:       OpsLens RBAC role assigned during SSO provisioning.
        ttl_seconds: Token lifetime in seconds (default: INTERNAL_TOKEN_TTL_SECONDS).

    Returns:
        Signed JWT string. Transmit only over HTTPS.
    """
    now = datetime.now(tz=timezone.utc)
    ttl = ttl_seconds if ttl_seconds is not None else _TTL_SECONDS

    payload = {
        "iss": _ISSUER,
        "sub": user_id,
        "email": email,
        "tenant_id": tenant_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)


def decode_sso_token(token: str) -> dict:
    """
    Validate and decode an internal SSO token.

    Raises jwt.InvalidTokenError (or subclasses) if the token is invalid,
    expired, or has a wrong issuer. Used by the JWT middleware for tokens
    whose header declares alg=HS256.
    """
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[_ALGORITHM],
        options={"verify_exp": True},
    )
    if payload.get("iss") != _ISSUER:
        raise jwt.InvalidTokenError(
            f"Expected issuer '{_ISSUER}', got '{payload.get('iss')}'."
        )
    return payload
