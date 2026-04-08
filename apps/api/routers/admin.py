"""
OpsLens AI — Admin Router
===========================
Team management, user directory, resource permissions, and invite flows.
All endpoints require the 'admin' role unless noted.

Endpoints:
    Teams:
        POST   /api/v1/admin/teams                              — Create team
        GET    /api/v1/admin/teams                              — List teams
        DELETE /api/v1/admin/teams/{team_id}                   — Delete team

    Team Members:
        POST   /api/v1/admin/teams/{team_id}/members           — Add member
        DELETE /api/v1/admin/teams/{team_id}/members/{user_id} — Remove member

    Team Permissions (data sources):
        GET    /api/v1/admin/teams/{team_id}/permissions        — List permissions
        PUT    /api/v1/admin/teams/{team_id}/permissions        — Set permissions (bulk replace)

    Users:
        GET    /api/v1/admin/users                             — List all org users
        PATCH  /api/v1/admin/users/{external_id}/role          — Promote / demote

    Invites:
        POST   /api/v1/admin/invites                           — Create & send invite
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth.dependencies import TenantContext, require_admin, require_member
from ..db.session import get_db
from ..db.models import Team, TeamMember, TeamResourcePermission, User, PendingInvite
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class TeamOut(BaseModel):
    id: str
    name: str
    description: str | None
    member_count: int
    created_at: str


class AddMemberRequest(BaseModel):
    user_external_id: str = Field(..., description="Clerk external_id of the user to add")
    user_email: str | None = None
    role: str = Field(default="member", pattern="^(member|admin)$")


class MemberOut(BaseModel):
    user_external_id: str
    user_email: str | None
    role: str
    added_at: str


class PermissionEntry(BaseModel):
    source_type: str = Field(..., description="'github' | 'jira' | 'slack' | 'confluence' | ...")
    source_id: str = Field(..., description="Repo name, project key, channel ID, etc.")
    can_read: bool = True
    can_see_metrics: bool = False
    can_see_logs: bool = False


class PermissionsUpdate(BaseModel):
    permissions: list[PermissionEntry]


class UserOut(BaseModel):
    external_id: str
    email: str
    name: str | None
    role: str
    created_at: str


class RoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(admin|member|viewer)$")


class InviteRequest(BaseModel):
    email: str
    team_id: str | None = None
    role: str = Field(default="member", pattern="^(admin|member|viewer)$")
    team_role: str = Field(default="member", pattern="^(member|admin)$")
    expires_in_hours: int = Field(default=72, ge=1, le=720)


class InviteOut(BaseModel):
    invite_id: str
    email: str
    token: str
    invite_url: str
    expires_at: str
    email_sent: bool


class PendingInviteOut(BaseModel):
    invite_id: str
    email: str
    role: str
    invite_url: str
    expires_at: str
    invited_by: str
    created_at: str


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.post("/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreate,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Create a new team within the tenant."""
    team = Team(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_uuid,
        name=body.name,
        description=body.description,
        created_by=ctx.user_id,
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    logger.info("Created team %s (%s) for tenant %s by %s", team.name, team.id, ctx.tenant_id, ctx.user_id)
    return TeamOut(
        id=str(team.id),
        name=team.name,
        description=team.description,
        member_count=0,
        created_at=team.created_at.isoformat(),
    )


@router.get("/teams", response_model=list[TeamOut])
async def list_teams(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """List all teams in the tenant."""
    result = await db.execute(
        sa.select(Team).where(Team.tenant_id == ctx.tenant_uuid).order_by(Team.created_at)
    )
    teams = result.scalars().all()

    # Batch-fetch member counts
    if teams:
        count_result = await db.execute(
            sa.select(TeamMember.team_id, sa.func.count(TeamMember.id).label("n"))
            .where(TeamMember.team_id.in_([t.id for t in teams]))
            .group_by(TeamMember.team_id)
        )
        counts = {str(row.team_id): row.n for row in count_result}
    else:
        counts = {}

    return [
        TeamOut(
            id=str(t.id),
            name=t.name,
            description=t.description,
            member_count=counts.get(str(t.id), 0),
            created_at=t.created_at.isoformat(),
        )
        for t in teams
    ]


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Delete a team and all its membership/permission records."""
    team = await _get_team_or_404(db, team_id, ctx.tenant_uuid)
    await db.delete(team)
    await db.commit()
    logger.info("Deleted team %s from tenant %s", team_id, ctx.tenant_id)


# ── Team Members ──────────────────────────────────────────────────────────────

@router.get("/teams/{team_id}/members", response_model=list[MemberOut])
async def list_team_members(
    team_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """List all members of a team."""
    await _get_team_or_404(db, team_id, ctx.tenant_uuid)
    result = await db.execute(
        sa.select(TeamMember)
        .where(TeamMember.team_id == uuid.UUID(team_id))
        .order_by(TeamMember.added_at)
    )
    members = result.scalars().all()
    return [
        MemberOut(
            user_external_id=m.user_external_id,
            user_email=m.user_email,
            role=m.role,
            added_at=m.added_at.isoformat(),
        )
        for m in members
    ]


@router.post("/teams/{team_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_team_member(
    team_id: str,
    body: AddMemberRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Add a user to a team. Idempotent — calling again updates the role."""
    await _get_team_or_404(db, team_id, ctx.tenant_uuid)

    # Check if already a member — update role if so
    existing = await db.execute(
        sa.select(TeamMember).where(
            TeamMember.team_id == uuid.UUID(team_id),
            TeamMember.user_external_id == body.user_external_id,
        )
    )
    member = existing.scalar_one_or_none()
    if member:
        member.role = body.role
        if body.user_email:
            member.user_email = body.user_email
    else:
        member = TeamMember(
            id=uuid.uuid4(),
            team_id=uuid.UUID(team_id),
            user_external_id=body.user_external_id,
            user_email=body.user_email,
            role=body.role,
            added_by=ctx.user_id,
        )
        db.add(member)

    await db.commit()
    await db.refresh(member)
    logger.info("Added %s to team %s (role=%s)", body.user_external_id[:12], team_id, body.role)
    return MemberOut(
        user_external_id=member.user_external_id,
        user_email=member.user_email,
        role=member.role,
        added_at=member.added_at.isoformat(),
    )


@router.delete("/teams/{team_id}/members/{user_external_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    team_id: str,
    user_external_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Remove a user from a team."""
    await _get_team_or_404(db, team_id, ctx.tenant_uuid)
    result = await db.execute(
        sa.select(TeamMember).where(
            TeamMember.team_id == uuid.UUID(team_id),
            TeamMember.user_external_id == user_external_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in team")
    await db.delete(member)
    await db.commit()
    logger.info("Removed %s from team %s", user_external_id[:12], team_id)


# ── Team Permissions ──────────────────────────────────────────────────────────

@router.get("/teams/{team_id}/permissions", response_model=list[PermissionEntry])
async def get_team_permissions(
    team_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """List all data source permissions granted to this team."""
    await _get_team_or_404(db, team_id, ctx.tenant_uuid)
    result = await db.execute(
        sa.select(TeamResourcePermission)
        .where(TeamResourcePermission.team_id == uuid.UUID(team_id))
        .order_by(TeamResourcePermission.source_type, TeamResourcePermission.source_id)
    )
    perms = result.scalars().all()
    return [
        PermissionEntry(
            source_type=p.source_type,
            source_id=p.source_id,
            can_read=p.can_read,
            can_see_metrics=p.can_see_metrics,
            can_see_logs=p.can_see_logs,
        )
        for p in perms
    ]


@router.put("/teams/{team_id}/permissions", response_model=list[PermissionEntry])
async def set_team_permissions(
    team_id: str,
    body: PermissionsUpdate,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Bulk-replace all permissions for a team.
    Existing permissions not in the new list are removed.
    """
    await _get_team_or_404(db, team_id, ctx.tenant_uuid)
    team_uuid = uuid.UUID(team_id)

    # Delete all existing permissions for this team
    await db.execute(
        sa.delete(TeamResourcePermission).where(TeamResourcePermission.team_id == team_uuid)
    )

    # Insert the new set
    new_perms: list[TeamResourcePermission] = []
    for entry in body.permissions:
        perm = TeamResourcePermission(
            id=uuid.uuid4(),
            team_id=team_uuid,
            source_type=entry.source_type,
            source_id=entry.source_id,
            can_read=entry.can_read,
            can_see_metrics=entry.can_see_metrics,
            can_see_logs=entry.can_see_logs,
        )
        db.add(perm)
        new_perms.append(perm)

    await db.commit()
    logger.info(
        "Set %d permissions for team %s in tenant %s",
        len(new_perms), team_id, ctx.tenant_id,
    )
    return [
        PermissionEntry(
            source_type=p.source_type,
            source_id=p.source_id,
            can_read=p.can_read,
            can_see_metrics=p.can_see_metrics,
            can_see_logs=p.can_see_logs,
        )
        for p in new_perms
    ]


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserOut])
async def list_users(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
    limit: int = 100,
    offset: int = 0,
):
    """List all users in the tenant."""
    result = await db.execute(
        sa.select(User)
        .where(User.tenant_id == ctx.tenant_uuid)
        .order_by(User.created_at)
        .limit(limit)
        .offset(offset)
    )
    users = result.scalars().all()
    return [
        UserOut(
            external_id=u.external_id,
            email=u.email,
            name=u.name,
            role=u.role,
            created_at=u.created_at.isoformat(),
        )
        for u in users
    ]


@router.patch("/users/{external_id}/role", response_model=UserOut)
async def update_user_role(
    external_id: str,
    body: RoleUpdate,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Promote or demote a user's tenant-level role. Admins cannot demote themselves."""
    if external_id == ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role.",
        )

    result = await db.execute(
        sa.select(User).where(
            User.tenant_id == ctx.tenant_uuid,
            User.external_id == external_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found in this tenant")

    old_role = user.role
    user.role = body.role
    await db.commit()
    await db.refresh(user)
    logger.info(
        "Role updated: %s %s → %s (by %s in tenant %s)",
        external_id[:12], old_role, body.role, ctx.user_id[:12], ctx.tenant_id,
    )
    return UserOut(
        external_id=user.external_id,
        email=user.email,
        name=user.name,
        role=user.role,
        created_at=user.created_at.isoformat(),
    )


# ── Invites ───────────────────────────────────────────────────────────────────

@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteRequest,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """
    Generate an invite token and email it to the recipient.

    The invite link points to /sign-up?token=<invite_id>. After completing
    Clerk sign-up the user lands on /join?token=<invite_id> which calls
    POST /api/v1/admin/invites/{invite_id}/redeem to activate their membership.
    """
    from ..config import settings
    from ..services.email_service import send_invite_email

    team_uuid: uuid.UUID | None = None
    if body.team_id:
        team = await _get_team_or_404(db, body.team_id, ctx.tenant_uuid)
        team_uuid = team.id

    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=body.expires_in_hours)
    invite = PendingInvite(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_uuid,
        team_id=team_uuid,
        email=body.email.strip().lower(),
        role=body.role,
        team_role=body.team_role,
        invited_by=ctx.user_id,
        expires_at=expires_at,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    invite_url = f"{settings.APP_URL}/sign-up?token={invite.id}"

    # Fetch workspace name and inviter display name for the email
    from ..db.models import Tenant
    tenant_result = await db.execute(
        sa.select(Tenant.name).where(Tenant.id == ctx.tenant_uuid)
    )
    workspace_name = tenant_result.scalar_one_or_none() or "your workspace"

    inviter_result = await db.execute(
        sa.select(User).where(
            User.tenant_id == ctx.tenant_uuid,
            User.external_id == ctx.user_id,
        )
    )
    inviter = inviter_result.scalar_one_or_none()
    invited_by_name = (inviter.name if inviter and inviter.name else None) or \
                      (inviter.email if inviter else ctx.user_id)

    email_sent = await send_invite_email(
        to_email=body.email,
        invited_by_name=invited_by_name,
        workspace_name=workspace_name,
        role=body.role,
        invite_url=invite_url,
        expires_hours=body.expires_in_hours,
    )

    logger.info(
        "Invite created for %s → tenant %s role=%s email_sent=%s (by %s)",
        body.email, ctx.tenant_id, body.role, email_sent, ctx.user_id[:12],
    )
    return InviteOut(
        invite_id=str(invite.id),
        email=invite.email,
        token=str(invite.id),
        invite_url=invite_url,
        expires_at=invite.expires_at.isoformat(),
        email_sent=email_sent,
    )


@router.get("/invites", response_model=list[PendingInviteOut])
async def list_pending_invites(
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """List all pending (not yet accepted) invites for this workspace."""
    from ..config import settings

    result = await db.execute(
        sa.select(PendingInvite)
        .where(
            PendingInvite.tenant_id == ctx.tenant_uuid,
            PendingInvite.accepted_at == None,  # noqa: E711
            PendingInvite.expires_at > datetime.now(tz=timezone.utc),
        )
        .order_by(PendingInvite.expires_at.desc())
    )
    invites = result.scalars().all()
    return [
        PendingInviteOut(
            invite_id=str(i.id),
            email=i.email,
            role=i.role,
            invite_url=f"{settings.APP_URL}/sign-up?token={i.id}",
            expires_at=i.expires_at.isoformat(),
            invited_by=str(i.invited_by),
            created_at=i.created_at.isoformat() if hasattr(i, "created_at") else "",
        )
        for i in invites
    ]


@router.post("/invites/{invite_id}/resend", status_code=status.HTTP_200_OK)
async def resend_invite(
    invite_id: str,
    ctx: Annotated[TenantContext, Depends(require_admin)],
    db=Depends(get_db),
):
    """Resend the invite email for an existing pending invite."""
    from ..config import settings
    from ..services.email_service import send_invite_email
    from ..db.models import Tenant

    result = await db.execute(
        sa.select(PendingInvite).where(
            PendingInvite.id == uuid.UUID(invite_id),
            PendingInvite.tenant_id == ctx.tenant_uuid,
            PendingInvite.accepted_at == None,  # noqa: E711
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found or already accepted")
    if invite.expires_at < datetime.now(tz=timezone.utc):
        raise HTTPException(status_code=410, detail="Invite has expired — create a new one")

    invite_url = f"{settings.APP_URL}/sign-up?token={invite.id}"

    tenant_result = await db.execute(
        sa.select(Tenant.name).where(Tenant.id == ctx.tenant_uuid)
    )
    workspace_name = tenant_result.scalar_one_or_none() or "your workspace"

    inviter_result = await db.execute(
        sa.select(User).where(
            User.tenant_id == ctx.tenant_uuid,
            User.external_id == ctx.user_id,
        )
    )
    inviter = inviter_result.scalar_one_or_none()
    invited_by_name = (inviter.name if inviter and inviter.name else None) or \
                      (inviter.email if inviter else ctx.user_id)

    hours_remaining = max(
        1,
        int((invite.expires_at - datetime.now(tz=timezone.utc)).total_seconds() // 3600),
    )
    email_sent = await send_invite_email(
        to_email=invite.email,
        invited_by_name=invited_by_name,
        workspace_name=workspace_name,
        role=invite.role,
        invite_url=invite_url,
        expires_hours=hours_remaining,
    )
    return {"status": "resent" if email_sent else "failed", "invite_url": invite_url}


@router.post("/invites/{invite_id}/redeem", status_code=status.HTTP_200_OK)
async def redeem_invite(
    invite_id: str,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """
    Redeem an invite token. Called by the frontend after the new user completes sign-up.
    - Sets the user's tenant role to the role specified in the invite.
    - Adds the user to the specified team with the specified team role.
    - Marks the invite as accepted.
    """
    result = await db.execute(
        sa.select(PendingInvite).where(
            PendingInvite.id == uuid.UUID(invite_id),
            PendingInvite.tenant_id == ctx.tenant_uuid,
            PendingInvite.accepted_at == None,  # noqa: E711
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found or already used")

    if invite.expires_at and invite.expires_at < datetime.now(tz=timezone.utc):
        raise HTTPException(status_code=410, detail="Invite link has expired")

    # Update the user's tenant role
    user_result = await db.execute(
        sa.select(User).where(
            User.tenant_id == ctx.tenant_uuid,
            User.external_id == ctx.user_id,
        )
    )
    user = user_result.scalar_one_or_none()
    if user and invite.role:
        user.role = invite.role

    # Add to team if specified
    if invite.team_id:
        existing = await db.execute(
            sa.select(TeamMember).where(
                TeamMember.team_id == invite.team_id,
                TeamMember.user_external_id == ctx.user_id,
            )
        )
        if not existing.scalar_one_or_none():
            db.add(TeamMember(
                id=uuid.uuid4(),
                team_id=invite.team_id,
                user_external_id=ctx.user_id,
                user_email=user.email if user else None,
                role=invite.team_role,
                added_by=str(invite.invited_by),
            ))

    invite.accepted_at = datetime.now(tz=timezone.utc)
    await db.commit()
    logger.info("Invite %s redeemed by %s in tenant %s", invite_id, ctx.user_id[:12], ctx.tenant_id)
    return {"status": "accepted", "role": invite.role, "team_id": str(invite.team_id) if invite.team_id else None}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_team_or_404(db, team_id: str, tenant_uuid) -> Team:
    try:
        tid = uuid.UUID(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found")

    result = await db.execute(
        sa.select(Team).where(Team.id == tid, Team.tenant_id == tenant_uuid)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team
