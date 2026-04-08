"""
OpsLens AI — FastAPI Auth Dependencies
========================================
Reusable Depends() helpers for RBAC enforcement.

Usage:
    from .auth.dependencies import require_admin, require_member, require_viewer

    @router.post("/something")
    async def my_endpoint(ctx: Annotated[TenantContext, Depends(require_admin)]):
        ...
"""
from __future__ import annotations

import re
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request, status

from ..db.session import get_db
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TenantContext:
    """
    Structured tenant + user context extracted from the validated JWT.
    Injected into every endpoint handler that uses a require_* dependency.

    tenant_id is stored as a str (UUID string) so it is compatible with both
    String-column models (newer) and UUID-column models (older db/models.py).
    Use .tenant_uuid when a real uuid.UUID object is required (UUID columns).
    """
    tenant_id:    str   # UUID string — works with String AND UUID columns via cast
    user_id:      str   # Clerk sub (string) — never used as a DB FK
    role:         str
    company_name: str

    @property
    def tenant_uuid(self) -> _uuid.UUID:
        """Return tenant_id as a uuid.UUID for UUID-typed DB columns."""
        return _uuid.UUID(self.tenant_id)


# Role hierarchy (higher index = more permissive)
_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2}


def _to_uuid(s: str) -> str:
    """
    Convert any string to a UUID string.
    If it's already a valid UUID, return it unchanged.
    Otherwise return a deterministic UUID5 derived from the string.
    This maps Clerk string IDs (user_2abc..., org_2abc...) to stable UUIDs.
    """
    try:
        return str(_uuid.UUID(s))
    except ValueError:
        return str(_uuid.uuid5(_uuid.NAMESPACE_URL, s))


async def _provision_tenant(db, tenant_id_str: str) -> str:
    """
    Ensure a Tenant row exists for this tenant_id string.
    Returns the internal UUID string to use for all DB operations.

    On first login, Clerk users have no DB tenant — this creates one automatically.
    """
    from ..db.models import Tenant

    tenant_uuid_str = _to_uuid(tenant_id_str)
    tenant_uuid_obj = _uuid.UUID(tenant_uuid_str)  # asyncpg needs a real uuid.UUID

    # Check if tenant already exists
    result = await db.execute(
        sa.select(Tenant.id).where(Tenant.id == tenant_uuid_obj)
    )
    if result.scalar_one_or_none() is not None:
        return tenant_uuid_str

    # Build a safe slug: lowercase alphanumeric + hyphens, max 99 chars
    safe_slug = re.sub(r"[^a-z0-9]+", "-", tenant_id_str.lower())
    safe_slug = safe_slug.strip("-")[:99] or "workspace"

    tenant = Tenant(
        id=tenant_uuid_obj,
        name=f"Workspace {tenant_id_str[:20]}",
        slug=safe_slug,
        plan="starter",
        settings={},
    )
    db.add(tenant)
    try:
        await db.commit()
        logger.info("Auto-provisioned tenant %s (slug=%s)", tenant_uuid_str, safe_slug)
    except Exception as exc:
        await db.rollback()
        logger.warning("Tenant provision skipped (likely already exists): %s", exc)
        # Re-check — it may have been created by another concurrent request
        result2 = await db.execute(
            sa.select(Tenant.id).where(Tenant.id == tenant_uuid_obj)
        )
        if result2.scalar_one_or_none() is None:
            # Truly failed — surface the error
            raise HTTPException(status_code=500, detail=f"Could not provision tenant: {exc}") from exc

    return tenant_uuid_str


async def _provision_user(db, tenant_uuid_str: str, external_id: str, email: str) -> str:
    """
    Ensure a User row exists for this Clerk user in the given tenant.
    - First user in a tenant → role='admin' (they're the owner who set it up)
    - Subsequent users       → role='member'

    Domain allowlist enforcement:
        If the tenant has configured allowed_email_domains (a non-empty list),
        only users whose email domain appears in that list will be provisioned.
        Existing users are never blocked — the check only applies to first login.
        Admins manage the allowlist via POST /api/v1/auth/domain-allowlist.

    Returns the user's role string.
    """
    from ..db.models import User, Tenant

    tenant_uuid_obj = _uuid.UUID(tenant_uuid_str)

    # Check if user already exists — existing users bypass all allowlist checks
    result = await db.execute(
        sa.select(User.role).where(
            User.tenant_id == tenant_uuid_obj,
            User.external_id == external_id,
        )
    )
    existing_role = result.scalar_one_or_none()
    if existing_role is not None:
        return existing_role

    # ── Domain allowlist check (new users only) ────────────────────────────────
    # Fetch the tenant's allowed_email_domains list from the DB.
    tenant_result = await db.execute(
        sa.select(Tenant.allowed_email_domains).where(Tenant.id == tenant_uuid_obj)
    )
    allowed_domains: list[str] = tenant_result.scalar_one_or_none() or []

    if allowed_domains:
        # Extract domain from the email address (case-insensitive comparison)
        email_domain = email.split("@")[-1].lower() if "@" in email else ""
        allowed_lower = [d.strip().lower() for d in allowed_domains]
        if email_domain not in allowed_lower:
            logger.warning(
                "Registration blocked: email domain '%s' not in allowlist for tenant %s. "
                "Allowed: %s",
                email_domain, tenant_uuid_str, allowed_lower,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Your email domain '{email_domain}' is not approved for this workspace. "
                    "Contact your administrator to request access."
                ),
            )

    # Count existing users in this tenant to decide role
    count_result = await db.execute(
        sa.select(sa.func.count()).select_from(User).where(User.tenant_id == tenant_uuid_obj)
    )
    user_count = count_result.scalar_one()

    # ── Invite-only enforcement (non-first users) ──────────────────────────────
    # The very first user in a tenant is the workspace owner provisioned by
    # OpsLens when onboarding a new customer — they bypass the invite check.
    # Every subsequent user must have a valid, unexpired, unused invite for
    # their email address.  This prevents public self-registration entirely.
    if user_count > 0:
        from ..db.models import PendingInvite

        email_normalised = email.strip().lower()
        invite_result = await db.execute(
            sa.select(PendingInvite).where(
                PendingInvite.tenant_id == tenant_uuid_obj,
                sa.func.lower(PendingInvite.email) == email_normalised,
                PendingInvite.accepted_at == None,  # noqa: E711
                PendingInvite.expires_at > datetime.now(tz=timezone.utc),
            )
        )
        valid_invite = invite_result.scalar_one_or_none()
        if not valid_invite:
            logger.warning(
                "Registration blocked: no valid invite for email '%s' in tenant %s",
                email_normalised, tenant_uuid_str,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Access by invitation only. "
                    "Ask your workspace admin to send you an invite link."
                ),
            )

    role = "admin" if user_count == 0 else "member"

    user = User(
        id=_uuid.uuid4(),
        tenant_id=tenant_uuid_obj,
        external_id=external_id,
        email=email or f"{external_id}@unknown.local",
        role=role,
    )
    db.add(user)
    try:
        await db.commit()
        logger.info("Auto-provisioned user %s in tenant %s with role=%s", external_id, tenant_uuid_str, role)
    except Exception as exc:
        await db.rollback()
        logger.warning("User provision skipped (likely already exists): %s", exc)
        # Re-fetch in case of race condition
        result2 = await db.execute(
            sa.select(User.role).where(
                User.tenant_id == tenant_uuid_obj,
                User.external_id == external_id,
            )
        )
        existing_role2 = result2.scalar_one_or_none()
        return existing_role2 or "member"

    return role


async def _build_ctx(request: Request, db, min_role: str) -> TenantContext:
    raw_tenant_id = getattr(request.state, "tenant_id", "")
    user_id       = getattr(request.state, "user_id", "")

    # 1. Ensure tenant row exists
    tenant_uuid = await _provision_tenant(db, raw_tenant_id)

    # 2. Look up (or auto-create) the user in the DB to get their actual role.
    #    Clerk JWTs don't include a 'role' claim by default, so we can't rely on
    #    the JWT value — the DB is the single source of truth for RBAC.
    email = getattr(request.state, "email", "") or f"{user_id}@unknown.local"
    role  = await _provision_user(db, tenant_uuid, user_id, email)

    if _ROLE_RANK.get(role, -1) < _ROLE_RANK[min_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions. Required role: {min_role}, your role: {role}.",
        )

    return TenantContext(
        tenant_id=tenant_uuid,   # str — compatible with String + UUID columns
        user_id=user_id,
        role=role,
        company_name=getattr(request.state, "company_name", "your company"),
    )


async def require_viewer(request: Request, db=Depends(get_db)) -> TenantContext:
    """Require at minimum the 'viewer' role (any authenticated user)."""
    return await _build_ctx(request, db, min_role="viewer")


async def require_member(request: Request, db=Depends(get_db)) -> TenantContext:
    """Require at minimum the 'member' role."""
    return await _build_ctx(request, db, min_role="member")


async def require_admin(request: Request, db=Depends(get_db)) -> TenantContext:
    """Require the 'admin' role."""
    return await _build_ctx(request, db, min_role="admin")
