"""
OpsLens AI — Public Router
=============================
Unauthenticated endpoints safe to expose to anonymous visitors — currently
just tenant branding, resolved by slug, so the sign-in page can show a
client's own logo before they've logged in.

Endpoints:
    GET /api/v1/public/tenants/{slug}/branding — Resolve a tenant's logo by slug
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db.models import Tenant
from ..db.session import get_db
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


class TenantBrandingOut(BaseModel):
    name: str
    logo_url: str | None


@router.get("/tenants/{slug}/branding", response_model=TenantBrandingOut)
async def get_tenant_branding(slug: str, db=Depends(get_db)):
    """
    Resolve a tenant's public branding (name + logo) by slug.

    Only non-sensitive fields are returned — this endpoint is reachable
    without authentication.
    """
    result = await db.execute(sa.select(Tenant).where(Tenant.slug == slug))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Workspace not found")

    logo_url = (tenant.settings or {}).get("branding", {}).get("logo_url")
    return TenantBrandingOut(name=tenant.name, logo_url=logo_url)
