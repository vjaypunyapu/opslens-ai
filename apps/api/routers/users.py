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

logger = get_logger(__name__)
router = APIRouter()

class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    role: str
    external_id: str
    model_config = {"from_attributes": True}

class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    model_config = {"from_attributes": True}

class UserUpdateBody(BaseModel):
    name: str | None = Field(None, max_length=255)
    role: str | None = Field(None, pattern="^(admin|member|viewer)$")

@router.get("/me", response_model=UserOut)
async def get_current_user(ctx: Annotated[TenantContext, Depends(require_viewer)], db=Depends(get_db)):
    result = await db.execute(sa.select(User).where(User.external_id == ctx.user_id, User.tenant_id == ctx.tenant_uuid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user

@router.get("/tenant", response_model=TenantOut)
async def get_tenant(ctx: Annotated[TenantContext, Depends(require_viewer)], db=Depends(get_db)):
    result = await db.execute(sa.select(Tenant).where(Tenant.id == ctx.tenant_uuid))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant

@router.get("/users", response_model=list[UserOut])
async def list_users(ctx: Annotated[TenantContext, Depends(require_admin)], db=Depends(get_db)):
    result = await db.execute(sa.select(User).where(User.tenant_id == ctx.tenant_uuid).order_by(User.email))
    return result.scalars().all()
