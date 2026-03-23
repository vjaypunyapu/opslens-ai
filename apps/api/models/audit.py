"""
OpsLens AI — Audit Log Model
==============================
Immutable event ledger for every write operation in the system.

Every POST / PATCH / DELETE that touches a resource creates one AuditLog row.
Used for:
  - SOC 2 / ISO 27001 compliance evidence
  - SAML + SCIM provisioning audit trail
  - RBAC permission change history
  - Incident response forensics ("who changed the routing rule 5 minutes before the outage?")

The table is append-only by convention — no UPDATE or DELETE is ever issued
against audit_logs. Retention is controlled by the RetentionPolicy model.

actor_id   — user_id from JWT (or "system" for automated tasks / SCIM)
actor_role — role at time of action (snapshot — role may change later)
resource   — e.g. "alert_routing_rule", "rrt_brief", "known_issue", "user"
resource_id — PK of the affected row
action     — "create" | "update" | "delete" | "login" | "logout" | "scim_provision"
             | "permission_change" | "saml_login" | "api_key_create" | "api_key_revoke"
before     — JSONB snapshot of the resource before the change (NULL for create)
after      — JSONB snapshot of the resource after the change (NULL for delete)
ip_address — client IP (from X-Forwarded-For or request.client.host)
user_agent — HTTP User-Agent header
request_id — X-Request-ID header (correlate with API access logs)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from apps.api.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "opslens"}

    id          = Column(String, primary_key=True)
    tenant_id   = Column(String, nullable=False, index=True)

    # Who
    actor_id    = Column(String, nullable=False, index=True)   # user_id or "system"
    actor_role  = Column(String, nullable=True)                # role snapshot

    # What
    resource    = Column(String, nullable=False, index=True)   # resource type
    resource_id = Column(String, nullable=True, index=True)    # affected row PK
    action      = Column(String, nullable=False, index=True)   # verb

    # Change diff (both nullable — create has no before, delete has no after)
    before      = Column(JSONB, nullable=True)
    after       = Column(JSONB, nullable=True)

    # Request context
    ip_address  = Column(String, nullable=True)
    user_agent  = Column(Text, nullable=True)
    request_id  = Column(String, nullable=True)

    # Immutable timestamp
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(tz=timezone.utc),
    )
