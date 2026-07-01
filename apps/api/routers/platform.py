"""
OpsLens AI — Platform Admin Router
====================================
Founder-level endpoints for managing all client workspaces from a single
privileged view. Gated by PLATFORM_ADMIN_EMAILS environment variable — only
the email addresses listed there can access these endpoints.

This solves the chicken-and-egg problem: you need to be inside a tenant to
invite its first admin, but you can't be inside a tenant you haven't created
yet. This router runs above the tenant boundary.

Endpoints:
    GET  /api/v1/platform/tenants                          — List all workspaces
    POST /api/v1/platform/tenants                          — Provision new workspace
    GET  /api/v1/platform/tenants/{tenant_id}              — Get workspace detail
    POST /api/v1/platform/tenants/{tenant_id}/invite       — Send first-admin invite
    GET  /api/v1/platform/tenants/{tenant_id}/invites      — List pending invites
    POST /api/v1/platform/tenants/{tenant_id}/impersonate  — Get a scoped JWT (dev only)

Security model:
    PLATFORM_ADMIN_EMAILS is a comma-separated list of email addresses.
    Any authenticated user whose email is in that list passes the
    _require_platform_admin dependency. All other users receive 403.

    This is intentionally simple — a whitelist in an env var is auditable,
    easy to reason about, and doesn't require a separate auth flow. Keep the
    list short (founder + co-founder only).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, get_db
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── Platform-admin guard ──────────────────────────────────────────────────────

async def _require_platform_admin(request: Request, db=Depends(get_db)) -> dict:
    """
    Dependency that restricts access to emails listed in PLATFORM_ADMIN_EMAILS.

    Returns a dict with {user_id, email} if the caller is a platform admin.
    Raises 403 otherwise.
    """
    from ..config import settings

    user_id = getattr(request.state, "user_id", "")
    email   = getattr(request.state, "email", "") or ""

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")

    admin_emails_raw = getattr(settings, "PLATFORM_ADMIN_EMAILS", "") or ""
    admin_emails = {e.strip().lower() for e in admin_emails_raw.split(",") if e.strip()}

    if not admin_emails:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PLATFORM_ADMIN_EMAILS is not configured. "
                "Set it in your environment to enable the platform admin panel."
            ),
        )

    if email.lower() not in admin_emails:
        logger.warning(
            "Platform admin access denied for user=%s email=%s — not in PLATFORM_ADMIN_EMAILS",
            user_id[:12], email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required.",
        )

    logger.info("Platform admin access granted: user=%s email=%s", user_id[:12], email)
    return {"user_id": user_id, "email": email}


PlatformAdmin = Annotated[dict, Depends(_require_platform_admin)]


# ── Schemas ───────────────────────────────────────────────────────────────────

class TenantSummary(BaseModel):
    id:                str
    name:              str
    slug:              str
    plan:              str
    created_at:        str
    user_count:        int
    integration_count: int
    insight_count:     int
    incident_count:    int
    last_activity_at:  str | None


class TenantDetail(TenantSummary):
    pending_invite_count: int
    active_integration_types: list[str]


class ProvisionTenantRequest(BaseModel):
    name:  str = Field(..., min_length=1, max_length=255, description="Client company name")
    plan:  str = Field(default="starter", pattern="^(starter|growth|enterprise)$")
    slug:  str | None = Field(
        default=None,
        description="URL-safe slug — auto-derived from name if omitted",
    )


class ProvisionTenantOut(BaseModel):
    tenant_id:   str
    name:        str
    slug:        str
    plan:        str
    created_at:  str
    sign_in_url: str


class LogoUploadOut(BaseModel):
    logo_url:    str
    sign_in_url: str


MAX_LOGO_BYTES = 1_000_000  # 1MB — logos are small; keeps the JSONB column and API payloads sane
ALLOWED_LOGO_TYPES = {"image/svg+xml", "image/png", "image/jpeg", "image/webp"}


class PlatformInviteRequest(BaseModel):
    email:            str
    role:             str = Field(default="admin", pattern="^(admin|member|viewer)$")
    expires_in_hours: int = Field(default=72, ge=1, le=720)


class PlatformInviteOut(BaseModel):
    invite_id:  str
    email:      str
    invite_url: str
    expires_at: str
    email_sent: bool


# ── List all tenants ──────────────────────────────────────────────────────────

@router.get("/tenants", response_model=list[TenantSummary])
async def list_tenants(
    caller: PlatformAdmin,
    db=Depends(get_db),
    limit: int = 100,
    offset: int = 0,
):
    """Return all workspaces with high-level usage stats."""
    from ..db.models import Tenant, User, Integration, Insight, Incident

    result = await db.execute(
        sa.select(Tenant)
        .order_by(Tenant.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    tenants = result.scalars().all()

    if not tenants:
        return []

    tenant_ids = [t.id for t in tenants]

    # Batch fetch counts in parallel queries
    user_counts_result = await db.execute(
        sa.select(User.tenant_id, sa.func.count(User.id).label("n"))
        .where(User.tenant_id.in_(tenant_ids))
        .group_by(User.tenant_id)
    )
    user_counts = {str(r.tenant_id): r.n for r in user_counts_result}

    int_counts_result = await db.execute(
        sa.select(Integration.tenant_id, sa.func.count(Integration.id).label("n"))
        .where(Integration.tenant_id.in_(tenant_ids), Integration.status == "active")
        .group_by(Integration.tenant_id)
    )
    int_counts = {str(r.tenant_id): r.n for r in int_counts_result}

    ins_counts_result = await db.execute(
        sa.select(Insight.tenant_id, sa.func.count(Insight.id).label("n"))
        .where(Insight.tenant_id.in_(tenant_ids))
        .group_by(Insight.tenant_id)
    )
    ins_counts = {str(r.tenant_id): r.n for r in ins_counts_result}

    inc_counts_result = await db.execute(
        sa.select(Incident.tenant_id, sa.func.count(Incident.id).label("n"))
        .where(Incident.tenant_id.in_(tenant_ids))
        .group_by(Incident.tenant_id)
    )
    inc_counts = {str(r.tenant_id): r.n for r in inc_counts_result}

    # Last activity = most recent incident started_at
    last_activity_result = await db.execute(
        sa.select(
            Incident.tenant_id,
            sa.func.max(Incident.started_at).label("last_at"),
        )
        .where(Incident.tenant_id.in_(tenant_ids))
        .group_by(Incident.tenant_id)
    )
    last_activity = {str(r.tenant_id): r.last_at for r in last_activity_result}

    return [
        TenantSummary(
            id=str(t.id),
            name=t.name,
            slug=t.slug or "",
            plan=t.plan or "starter",
            created_at=t.created_at.isoformat() if t.created_at else "",
            user_count=user_counts.get(str(t.id), 0),
            integration_count=int_counts.get(str(t.id), 0),
            insight_count=ins_counts.get(str(t.id), 0),
            incident_count=inc_counts.get(str(t.id), 0),
            last_activity_at=(
                last_activity[str(t.id)].isoformat()
                if last_activity.get(str(t.id)) else None
            ),
        )
        for t in tenants
    ]


# ── Get tenant detail ─────────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
async def get_tenant_detail(
    tenant_id: str,
    caller: PlatformAdmin,
    db=Depends(get_db),
):
    """Return detailed stats for a single workspace."""
    from ..db.models import Tenant, User, Integration, Insight, Incident, PendingInvite

    tid = uuid.UUID(tenant_id)

    result = await db.execute(sa.select(Tenant).where(Tenant.id == tid))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    user_count = (await db.execute(
        sa.select(sa.func.count(User.id)).where(User.tenant_id == tid)
    )).scalar_one()

    int_result = await db.execute(
        sa.select(Integration.source_type, Integration.status)
        .where(Integration.tenant_id == tid, Integration.status == "active")
    )
    active_integrations = int_result.all()

    ins_count = (await db.execute(
        sa.select(sa.func.count(Insight.id)).where(Insight.tenant_id == tid)
    )).scalar_one()

    inc_count = (await db.execute(
        sa.select(sa.func.count(Incident.id)).where(Incident.tenant_id == tid)
    )).scalar_one()

    pending_invites = (await db.execute(
        sa.select(sa.func.count(PendingInvite.id)).where(
            PendingInvite.tenant_id == tid,
            PendingInvite.accepted_at == None,  # noqa: E711
            PendingInvite.expires_at > datetime.now(tz=timezone.utc),
        )
    )).scalar_one()

    last_activity_result = await db.execute(
        sa.select(sa.func.max(Incident.started_at)).where(Incident.tenant_id == tid)
    )
    last_at = last_activity_result.scalar_one()

    return TenantDetail(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug or "",
        plan=tenant.plan or "starter",
        created_at=tenant.created_at.isoformat() if tenant.created_at else "",
        user_count=user_count,
        integration_count=len(active_integrations),
        insight_count=ins_count,
        incident_count=inc_count,
        last_activity_at=last_at.isoformat() if last_at else None,
        pending_invite_count=pending_invites,
        active_integration_types=[r.source_type for r in active_integrations],
    )


# ── Provision new tenant ──────────────────────────────────────────────────────

@router.post("/tenants", response_model=ProvisionTenantOut, status_code=status.HTTP_201_CREATED)
async def provision_tenant(
    body: ProvisionTenantRequest,
    caller: PlatformAdmin,
    db=Depends(get_db),
):
    """
    Create a new client workspace.

    After provisioning, call POST /platform/tenants/{tenant_id}/invite with
    the client's admin email to send them their first-login invite link.
    """
    import re
    from ..config import settings as app_settings
    from ..db.models import Tenant

    slug = body.slug
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:80] or "workspace"

    # Ensure slug is unique — append random suffix if taken
    existing = (await db.execute(
        sa.select(Tenant.id).where(Tenant.slug == slug)
    )).scalar_one_or_none()
    if existing:
        slug = f"{slug}-{str(uuid.uuid4())[:6]}"

    tenant = Tenant(
        id=uuid.uuid4(),
        name=body.name,
        slug=slug,
        plan=body.plan,
        settings={},
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    logger.info(
        "Platform admin %s provisioned tenant %s (%s, plan=%s)",
        caller["email"], tenant.id, body.name, body.plan,
    )

    return ProvisionTenantOut(
        tenant_id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        plan=tenant.plan,
        created_at=tenant.created_at.isoformat(),
        sign_in_url=f"{app_settings.APP_URL}/sign-in?org={tenant.slug}",
    )


# ── Upload tenant logo ────────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/branding/logo", response_model=LogoUploadOut, status_code=status.HTTP_200_OK)
async def upload_tenant_logo(
    tenant_id: str,
    caller: PlatformAdmin,
    db=Depends(get_db),
    file: UploadFile = File(...),
):
    """
    Upload a company logo for a tenant, shown above the OpsLens AI branding
    on that tenant's dedicated sign-in link (/sign-in?org=<slug>).

    Stored in object storage (see ..utils.storage) under
    tenant-logos/{tenant_id}/{uuid}.{ext} — only the object key is persisted
    on the tenant; a fresh presigned URL is generated on read (see
    routers/public.py) so the bucket never needs to be public.
    """
    from ..config import settings as app_settings
    from ..db.models import Tenant
    from ..utils.storage import presigned_url, upload_object

    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported logo type '{file.content_type}'. Use SVG, PNG, JPEG, or WebP.",
        )

    data = await file.read()
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Logo must be under {MAX_LOGO_BYTES // 1000}KB.",
        )

    tid = uuid.UUID(tenant_id)
    result = await db.execute(sa.select(Tenant).where(Tenant.id == tid))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    ext = {
        "image/svg+xml": "svg", "image/png": "png",
        "image/jpeg": "jpg", "image/webp": "webp",
    }[file.content_type]
    key = f"tenant-logos/{tenant_id}/{uuid.uuid4().hex}.{ext}"
    upload_object(key, data, file.content_type)

    # Replace branding wholesale — a fresh upload supersedes any prior
    # upload (logo_key) or manually-entered URL (logo_url) from Settings.
    tenant.settings = {**tenant.settings, "branding": {"logo_key": key}}
    await db.commit()

    logger.info(
        "Platform admin %s uploaded logo for tenant %s (%d bytes, %s, key=%s)",
        caller["email"], tenant_id, len(data), file.content_type, key,
    )

    return LogoUploadOut(
        logo_url=presigned_url(key),
        sign_in_url=f"{app_settings.APP_URL}/sign-in?org={tenant.slug}",
    )


# ── Send first-admin invite to any tenant ─────────────────────────────────────

@router.post("/tenants/{tenant_id}/invite", response_model=PlatformInviteOut, status_code=status.HTTP_201_CREATED)
async def invite_to_tenant(
    tenant_id: str,
    body: PlatformInviteRequest,
    caller: PlatformAdmin,
    db=Depends(get_db),
):
    """
    Generate and email an invite link for any workspace.

    This is the main founder workflow:
      1. Call POST /platform/tenants to create the client workspace (or use an existing tenant_id)
      2. Call POST /platform/tenants/{id}/invite with the client's email
      3. Share the invite_url with the client — they click it, sign up via Clerk, and land in their workspace

    Unlike the regular invite endpoint (which requires you to be inside the tenant),
    this endpoint works from outside the tenant boundary.
    """
    from ..config import settings
    from ..services.email_service import send_invite_email
    from ..db.models import Tenant, PendingInvite

    tid = uuid.UUID(tenant_id)

    tenant_result = await db.execute(sa.select(Tenant).where(Tenant.id == tid))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=body.expires_in_hours)
    invite = PendingInvite(
        id=uuid.uuid4(),
        tenant_id=tid,
        team_id=None,
        email=body.email.strip().lower(),
        role=body.role,
        team_role="admin",
        invited_by=caller["user_id"],
        expires_at=expires_at,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    invite_url = f"{settings.APP_URL}/sign-up?token={invite.id}"

    email_sent = await send_invite_email(
        to_email=body.email,
        invited_by_name=caller["email"],
        workspace_name=tenant.name,
        role=body.role,
        invite_url=invite_url,
        expires_hours=body.expires_in_hours,
    )

    logger.info(
        "Platform admin %s invited %s to tenant %s (%s) as %s — email_sent=%s",
        caller["email"], body.email, tenant_id, tenant.name, body.role, email_sent,
    )

    return PlatformInviteOut(
        invite_id=str(invite.id),
        email=invite.email,
        invite_url=invite_url,
        expires_at=invite.expires_at.isoformat(),
        email_sent=email_sent,
    )


# ── List pending invites for a tenant ─────────────────────────────────────────

@router.get("/tenants/{tenant_id}/invites", response_model=list[PlatformInviteOut])
async def list_tenant_invites(
    tenant_id: str,
    caller: PlatformAdmin,
    db=Depends(get_db),
):
    """List all active (not yet accepted, not expired) invites for a workspace."""
    from ..config import settings
    from ..db.models import PendingInvite

    tid = uuid.UUID(tenant_id)
    result = await db.execute(
        sa.select(PendingInvite).where(
            PendingInvite.tenant_id == tid,
            PendingInvite.accepted_at == None,  # noqa: E711
            PendingInvite.expires_at > datetime.now(tz=timezone.utc),
        ).order_by(PendingInvite.expires_at.desc())
    )
    invites = result.scalars().all()

    return [
        PlatformInviteOut(
            invite_id=str(i.id),
            email=i.email,
            invite_url=f"{settings.APP_URL}/sign-up?token={i.id}",
            expires_at=i.expires_at.isoformat(),
            email_sent=True,  # historical — sent at creation time
        )
        for i in invites
    ]
