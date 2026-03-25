"""
OpsLens AI — Permission Resolution Service
============================================
Resolves what data sources a given user is allowed to access based on their
team memberships and the permissions assigned to those teams.

Design:
  - Tenant admins bypass all filtering — they see everything.
  - Regular users see the union of permissions granted to all their teams.
  - Three permission flags per source:
      can_read        → can query/retrieve documents from this source
      can_see_metrics → can view cost/latency telemetry scoped to this source
      can_see_logs    → can view log scan results from this source
  - Returns None when the user is unrestricted (admin), or a list of
    {source_type, source_id} dicts when restricted.

Usage:
    from .permissions import get_allowed_sources

    allowed = await get_allowed_sources(ctx.user_id, ctx.tenant_id, db)
    # None  → unrestricted (admin)
    # []    → no sources (new user with no team)
    # [...]  → specific sources
"""
from __future__ import annotations

import uuid
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ..utils.logging import get_logger

logger = get_logger(__name__)

PermissionFlag = Literal["can_read", "can_see_metrics", "can_see_logs"]


async def get_allowed_sources(
    user_external_id: str,
    tenant_id: str,
    db: AsyncSession,
    permission: PermissionFlag = "can_read",
) -> list[dict] | None:
    """
    Resolve the data sources this user may access.

    Returns:
        None  — user is a tenant admin; caller should apply NO filter (full access).
        list  — list of {"source_type": str, "source_id": str} dicts the user may access.
                An empty list means the user has no team membership (query will return nothing).

    Args:
        user_external_id: The Clerk external_id stored in TenantContext.user_id.
        tenant_id:        Tenant UUID string (TenantContext.tenant_id).
        db:               Async SQLAlchemy session.
        permission:       Which permission flag to check ("can_read", "can_see_metrics", "can_see_logs").
    """
    from ..db.models import User, TeamMember, TeamResourcePermission, Team

    tenant_uuid = uuid.UUID(tenant_id)

    # ── 1. Check if the user is a tenant admin ─────────────────────────────────
    result = await db.execute(
        sa.select(User.role).where(
            User.tenant_id == tenant_uuid,
            User.external_id == user_external_id,
        )
    )
    user_role = result.scalar_one_or_none()

    if user_role == "admin":
        logger.debug("permissions: user %s is tenant admin → unrestricted", user_external_id[:12])
        return None  # Unrestricted

    # ── 2. Gather allowed sources from all team memberships ────────────────────
    # Build a JOIN: team_members → teams → team_resource_permissions
    # Filter: user matches, teams belong to this tenant, and the permission flag is true
    perm_col = getattr(TeamResourcePermission, permission)

    rows = await db.execute(
        sa.select(
            TeamResourcePermission.source_type,
            TeamResourcePermission.source_id,
        )
        .join(Team, TeamResourcePermission.team_id == Team.id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(
            Team.tenant_id == tenant_uuid,
            TeamMember.user_external_id == user_external_id,
            perm_col == True,  # noqa: E712 — SQLAlchemy requires == not `is`
        )
        .distinct()
    )

    sources = [
        {"source_type": row.source_type, "source_id": row.source_id}
        for row in rows.fetchall()
    ]

    logger.debug(
        "permissions: user %s | %s → %d allowed sources",
        user_external_id[:12], permission, len(sources),
    )
    return sources


async def get_allowed_source_ids(
    user_external_id: str,
    tenant_id: str,
    db: AsyncSession,
    permission: PermissionFlag = "can_read",
) -> list[str] | None:
    """
    Convenience wrapper that returns just the source_id values (not dicts).
    Returns None for unrestricted admins.
    """
    sources = await get_allowed_sources(user_external_id, tenant_id, db, permission)
    if sources is None:
        return None
    return [s["source_id"] for s in sources]
