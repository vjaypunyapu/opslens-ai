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
    """
    tenant_id:    _uuid.UUID  # always a valid uuid.UUID — safe to pass to asyncpg columns
    user_id:      str          # Clerk sub (string) — never used as a DB FK
    role:         str
    company_name: str


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


async def _build_ctx(request: Request, db, min_role: str) -> TenantContext:
    role = getattr(request.state, "role", "member")
    if _ROLE_RANK.get(role, -1) < _ROLE_RANK[min_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions. Required role: {min_role}, your role: {role}.",
        )

    raw_tenant_id = getattr(request.state, "tenant_id", "")
    tenant_uuid = await _provision_tenant(db, raw_tenant_id)

    return TenantContext(
        tenant_id=_uuid.UUID(tenant_uuid),   # always a uuid.UUID object for asyncpg
        user_id=getattr(request.state, "user_id", ""),
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
