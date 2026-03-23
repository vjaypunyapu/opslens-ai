"""
OpsLens AI — Settings Router
================================
Tenant and user settings management.

Endpoints:
    GET    /api/v1/settings/tenant         — Get tenant settings
    PATCH  /api/v1/settings/tenant         — Update tenant name/settings
    GET    /api/v1/settings/members        — List team members
    POST   /api/v1/settings/members        — Invite/upsert a member
    PATCH  /api/v1/settings/members/{id}   — Update member role
    DELETE /api/v1/settings/members/{id}   — Remove member
    GET    /api/v1/settings/profile        — Get current user profile
    PATCH  /api/v1/settings/profile        — Update profile name
"""
from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin, require_viewer
from ..db.models import Tenant, User
from ..db.session import get_db
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── Schemas ───────────────────────────────────────────────────────────────────

class TenantUpdateBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    settings: dict | None = None


class MemberInviteBody(BaseModel):
    email: str = Field(..., max_length=255)
    name: str | None = Field(None, max_length=255)
    role: str = Field("member", pattern="^(admin|member|viewer)$")


class MemberUpdateBody(BaseModel):
    role: str = Field(..., pattern="^(admin|member|viewer)$")


class ProfileUpdateBody(BaseModel):
    name: str | None = Field(None, max_length=255)


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    settings: dict
    model_config = {"from_attributes": True}


class MemberOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    role: str
    external_id: str
    model_config = {"from_attributes": True}


# ── Tenant settings ───────────────────────────────────────────────────────────

@router.get("/settings/tenant", response_model=TenantOut)
async def get_tenant_settings(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    tenant = await _get_tenant(db, ctx.tenant_uuid)
    return tenant


@router.patch("/settings/tenant", response_model=TenantOut)
async def update_tenant_settings(
    body: TenantUpdateBody,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    tenant = await _get_tenant(db, ctx.tenant_uuid)
    if body.name is not None:
        tenant.name = body.name
    if body.settings is not None:
        tenant.settings = {**tenant.settings, **body.settings}
    await db.commit()
    await db.refresh(tenant)
    logger.info("Tenant %s settings updated by %s", ctx.tenant_uuid, ctx.user_id)
    return tenant


# ── Members ───────────────────────────────────────────────────────────────────

@router.get("/settings/members", response_model=list[MemberOut])
async def list_members(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    result = await db.execute(
        sa.select(User)
        .where(User.tenant_id == ctx.tenant_uuid)
        .order_by(User.email)
    )
    return result.scalars().all()


@router.post("/settings/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def invite_member(
    body: MemberInviteBody,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Create or update a team member record (no actual email send — that's handled by Clerk)."""
    existing = (await db.execute(
        sa.select(User).where(
            User.tenant_id == ctx.tenant_uuid,
            User.email == body.email,
        )
    )).scalar_one_or_none()

    if existing:
        existing.role = body.role
        if body.name:
            existing.name = body.name
        await db.commit()
        await db.refresh(existing)
        return existing

    user = User(
        tenant_id=ctx.tenant_uuid,
        external_id=f"pending_{uuid.uuid4().hex[:12]}",  # placeholder until they sign in
        email=body.email,
        name=body.name,
        role=body.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Member %s invited to tenant %s", body.email, ctx.tenant_uuid)
    return user


@router.patch("/settings/members/{member_id}", response_model=MemberOut)
async def update_member(
    member_id: uuid.UUID,
    body: MemberUpdateBody,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    member = await _get_member(db, member_id, ctx.tenant_uuid)
    member.role = body.role
    await db.commit()
    await db.refresh(member)
    return member


@router.delete("/settings/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: uuid.UUID,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    member = await _get_member(db, member_id, ctx.tenant_uuid)
    # Don't allow removing yourself
    if member.external_id == ctx.user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself.")
    await db.delete(member)
    await db.commit()


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/settings/profile", response_model=MemberOut)
async def get_profile(
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    result = await db.execute(
        sa.select(User).where(
            User.external_id == ctx.user_id,
            User.tenant_id == ctx.tenant_uuid,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return user


@router.patch("/settings/profile", response_model=MemberOut)
async def update_profile(
    body: ProfileUpdateBody,
    ctx: Annotated[TenantContext, Depends(require_viewer)],
    db=Depends(get_db),
):
    result = await db.execute(
        sa.select(User).where(
            User.external_id == ctx.user_id,
            User.tenant_id == ctx.tenant_uuid,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Profile not found.")
    if body.name is not None:
        user.name = body.name
    await db.commit()
    await db.refresh(user)
    return user


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_tenant(db, tenant_id: uuid.UUID) -> Tenant:
    result = await db.execute(sa.select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


async def _get_member(db, member_id: uuid.UUID, tenant_id: uuid.UUID) -> User:
    result = await db.execute(
        sa.select(User).where(User.id == member_id, User.tenant_id == tenant_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found.")
    return member
