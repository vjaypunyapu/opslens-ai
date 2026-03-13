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

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status


@dataclass(frozen=True)
class TenantContext:
    """
    Structured tenant + user context extracted from the validated JWT.
    Injected into every endpoint handler that uses a require_* dependency.
    """
    tenant_id:    str
    user_id:      str
    role:         str
    company_name: str


# Role hierarchy (higher index = more permissive)
_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2}


def _build_ctx(request: Request, min_role: str) -> TenantContext:
    role = getattr(request.state, "role", "viewer")
    if _ROLE_RANK.get(role, -1) < _ROLE_RANK[min_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions. Required role: {min_role}, your role: {role}.",
        )
    return TenantContext(
        tenant_id=getattr(request.state, "tenant_id", ""),
        user_id=getattr(request.state, "user_id", ""),
        role=role,
        company_name=getattr(request.state, "company_name", "your company"),
    )


def require_viewer(request: Request) -> TenantContext:
    """Require at minimum the 'viewer' role (any authenticated user)."""
    return _build_ctx(request, min_role="viewer")


def require_member(request: Request) -> TenantContext:
    """Require at minimum the 'member' role (viewer cannot use this)."""
    return _build_ctx(request, min_role="member")


def require_admin(request: Request) -> TenantContext:
    """Require the 'admin' role (only admins can use this)."""
    return _build_ctx(request, min_role="admin")
