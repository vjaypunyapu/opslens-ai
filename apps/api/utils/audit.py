"""
OpsLens AI — Shared Audit Helper
==================================
Extracted from enterprise.py so any router can emit audit events
without importing the full enterprise module.

Usage:
    from apps.api.utils.audit import write_audit

    await write_audit(
        db,
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        actor_role=ctx.role,
        resource="alert_routing_rule",
        resource_id=str(rr.id),
        action="create",
        after={"team_name": rr.team_name, "priority": rr.priority},
        request=request,   # FastAPI Request — extracts IP + UA automatically
    )
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def write_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str | None = None,
    resource: str,
    resource_id: str | None = None,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request: Request | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> None:
    """
    Insert one immutable AuditLog row.

    Never raises — silently logs on error so a failed audit write never
    blocks or rolls back the actual operation.

    If `request` is provided, IP and User-Agent are extracted automatically
    (honours X-Forwarded-For for Railway / Vercel proxy deployments).
    """
    try:
        from apps.api.models.audit import AuditLog

        # Extract request context if available
        if request is not None:
            # Honour proxy header from Railway / Vercel
            forwarded = request.headers.get("x-forwarded-for")
            ip_address = ip_address or (
                forwarded.split(",")[0].strip() if forwarded else
                (request.client.host if request.client else None)
            )
            user_agent = user_agent or request.headers.get("user-agent")
            request_id = request_id or request.headers.get("x-request-id")

        row = AuditLog(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_role=actor_role,
            resource=resource,
            resource_id=resource_id,
            action=action,
            before=before,
            after=after,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )
        db.add(row)
        # Deliberately NOT committing here — caller handles the transaction.
        # If caller commits, the audit row goes with it (same transaction).
        # This ensures audit rows are never written for operations that rolled back.

    except Exception:
        logger.exception(
            "audit write failed (non-fatal): resource=%s action=%s tenant=%s",
            resource, action, tenant_id,
        )
