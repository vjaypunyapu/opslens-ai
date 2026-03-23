"""
OpsLens AI — RBAC Models
==========================
Role-Based Access Control for multi-tenant enterprise deployments.

Roles (fixed, not tenant-configurable — keeps permission logic simple):
    admin    — full read/write across all resources; can manage users, SSO, SCIM
    manager  — read-only access to all data + manager dashboard endpoints
               cannot modify routing rules, known issues, or user roles
    engineer — read/write on operational resources (alerts, routing, known issues,
               RRT briefs); cannot manage users or SSO settings
    viewer   — read-only on all operational resources; no write access

Role hierarchy (coarse, used in require_roles dependency):
    admin > manager ≥ engineer > viewer

APIKey
    Service-account keys for CI/CD pipelines, monitoring agents, and
    automation scripts. Each key is scoped to one role and can be rotated
    or revoked without touching the user's login session.

    key_hash   — SHA-256 of the raw key (stored; never the raw value)
    key_prefix — first 8 chars of raw key shown in UI ("sk-oplen_…")
    last_used_at — updated on each successful auth (rate-limited update)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY

from apps.api.db.base import Base


# Valid role values — referenced by middleware and auth deps
VALID_ROLES = frozenset({"admin", "manager", "engineer", "viewer"})

# Role hierarchy for >= comparisons
ROLE_RANK: dict[str, int] = {
    "admin":    40,
    "manager":  30,
    "engineer": 20,
    "viewer":   10,
}


class RoleAssignment(Base):
    """
    Maps a user to a role within a tenant.
    A user can have different roles in different tenants (MSP / agency use case).
    If a user has no explicit RoleAssignment, the JWT `role` claim is used.
    A row here always overrides the JWT claim.
    """
    __tablename__ = "role_assignments"
    __table_args__ = {"schema": "opslens"}

    id          = Column(String, primary_key=True)
    tenant_id   = Column(String, nullable=False, index=True)
    user_id     = Column(String, nullable=False, index=True)
    role        = Column(String, nullable=False, default="viewer")    # see VALID_ROLES
    granted_by  = Column(String, nullable=True)                       # admin user_id
    granted_at  = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(tz=timezone.utc))
    expires_at  = Column(DateTime(timezone=True), nullable=True)      # NULL = permanent
    is_active   = Column(Boolean, nullable=False, default=True)
    notes       = Column(Text, nullable=True)


class APIKey(Base):
    """
    Service-account API key for CI/CD pipelines and automation.

    Create flow:
        1. Generate 32-byte random key  →  raw_key = "sk-oplen_<base64url(bytes)>"
        2. SHA-256(raw_key)             →  stored in key_hash
        3. raw_key[:12]                 →  stored in key_prefix (display only)
        4. Return raw_key ONCE to caller — never stored in plaintext.

    Auth flow (JWTAuthMiddleware):
        1. Extract `Authorization: Bearer sk-oplen_…` header
        2. SHA-256 the value, look up key_hash in DB
        3. Check is_active, expires_at, tenant_id
        4. Inject synthetic JWT claims: tenant_id, role=key_role, user_id=key_id
    """
    __tablename__ = "api_keys"
    __table_args__ = {"schema": "opslens"}

    id              = Column(String, primary_key=True)
    tenant_id       = Column(String, nullable=False, index=True)
    name            = Column(String, nullable=False)                # "GitHub Actions deploy key"
    description     = Column(Text, nullable=True)
    key_hash        = Column(String, nullable=False, unique=True)   # SHA-256 of raw key
    key_prefix      = Column(String, nullable=False)               # first 12 chars (display)
    role            = Column(String, nullable=False, default="engineer")
    scopes          = Column(ARRAY(String), nullable=False, default=list)
    created_by      = Column(String, nullable=True)                # user_id of creator
    is_active       = Column(Boolean, nullable=False, default=True)
    expires_at      = Column(DateTime(timezone=True), nullable=True)
    last_used_at    = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), nullable=False,
                             default=lambda: datetime.now(tz=timezone.utc))
