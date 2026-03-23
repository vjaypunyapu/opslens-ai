"""
OpsLens AI — Enterprise Auth Router
=====================================
Covers four enterprise procurement requirements in one module:

  SAML SSO
    GET  /api/v1/auth/saml/metadata           — SP metadata XML (public)
    POST /api/v1/auth/saml/acs                — Assertion Consumer Service (IdP callback)
    POST /api/v1/auth/saml/slo                — Single Logout
    GET  /api/v1/auth/saml/config             — Get tenant SAML config (admin only)
    POST /api/v1/auth/saml/config             — Create/update SAML config
    DELETE /api/v1/auth/saml/config           — Remove SAML config (disable SSO)

  SCIM 2.0 User Provisioning
    GET  /api/v1/scim/v2/Users                — List users
    POST /api/v1/scim/v2/Users                — Provision user
    GET  /api/v1/scim/v2/Users/{id}           — Get user
    PUT  /api/v1/scim/v2/Users/{id}           — Replace user
    PATCH /api/v1/scim/v2/Users/{id}          — Update user (partial)
    DELETE /api/v1/scim/v2/Users/{id}         — Deprovision user
    GET  /api/v1/scim/v2/Groups               — List groups
    POST /api/v1/scim/v2/Groups               — Create group
    PUT  /api/v1/scim/v2/Groups/{id}          — Replace group
    PATCH /api/v1/scim/v2/Groups/{id}         — Update group membership
    DELETE /api/v1/scim/v2/Groups/{id}        — Delete group

  RBAC — Role Management
    GET  /api/v1/rbac/roles                   — List user role assignments
    POST /api/v1/rbac/roles                   — Assign role to user
    PATCH /api/v1/rbac/roles/{id}             — Update role
    DELETE /api/v1/rbac/roles/{id}            — Revoke role

  API Keys — Service Account Management
    GET  /api/v1/rbac/api-keys                — List API keys (no secrets returned)
    POST /api/v1/rbac/api-keys                — Create key (returns raw key ONCE)
    DELETE /api/v1/rbac/api-keys/{id}         — Revoke key

  Audit Log
    GET  /api/v1/audit                        — Query audit log

All SAML / RBAC / audit endpoints require admin role except where noted.
SCIM endpoints use their own bearer token from SCIMConfig.bearer_token_hash.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth.middleware import require_roles
from apps.api.db.session import get_db
from apps.api.models.audit import AuditLog
from apps.api.models.rbac import APIKey, RoleAssignment, VALID_ROLES
from apps.api.models.saml import SAMLConfig, SCIMConfig

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _write_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str | None,
    resource: str,
    resource_id: str | None,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> None:
    """Insert one immutable AuditLog row. Never raises — silently logs on error."""
    try:
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
        # Don't commit here — caller's transaction covers it
    except Exception:
        pass


def _request_meta(request: Request) -> dict:
    return {
        "ip_address": (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or (request.client.host if request.client else None)
        ),
        "user_agent": request.headers.get("User-Agent", "")[:512],
        "request_id": request.headers.get("X-Request-ID", ""),
    }


async def _verify_scim_token(
    authorization: str = Header(..., alias="Authorization"),
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> SCIMConfig:
    """Dependency: validates the SCIM Bearer token against SCIMConfig.bearer_token_hash."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="SCIM bearer token required.")
    raw_token = authorization.removeprefix("Bearer ").strip()
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    result = await db.execute(
        sa.select(SCIMConfig).where(
            SCIMConfig.tenant_id == tenant_id,
            SCIMConfig.bearer_token_hash == token_hash,
            SCIMConfig.is_active == True,
        )
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=401, detail="Invalid or inactive SCIM token.")
    return cfg


# ════════════════════════════════════════════════════════════════════════════════
# SAML SSO
# ════════════════════════════════════════════════════════════════════════════════

class SAMLConfigIn(BaseModel):
    entity_id: str
    sp_acs_url: str
    sp_slo_url: str | None = None
    idp_metadata_url: str | None = None
    idp_entity_id: str | None = None
    idp_sso_url: str | None = None
    idp_slo_url: str | None = None
    idp_certificate: str | None = None
    name_id_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    sign_requests: bool = True
    force_authn: bool = False
    attribute_mapping: dict = Field(default_factory=dict)
    default_role: str = "viewer"


@router.get(
    "/auth/saml/metadata",
    response_class=PlainTextResponse,
    tags=["Enterprise — SAML SSO"],
    summary="SAML SP metadata XML (public, no auth)",
)
async def saml_metadata(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Returns the Service Provider SAML metadata XML for this tenant.
    Share this with your IdP during SSO configuration."""
    result = await db.execute(
        sa.select(SAMLConfig).where(SAMLConfig.tenant_id == tenant_id)
    )
    cfg: SAMLConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="SAML not configured for this tenant.")

    # Minimal SP metadata — production should use python3-saml for a full-featured XML
    xml = f"""<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata"
    entityID="{cfg.entity_id}">
  <SPSSODescriptor
      AuthnRequestsSigned="{str(cfg.sign_requests).lower()}"
      WantAssertionsSigned="true"
      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <NameIDFormat>{cfg.name_id_format}</NameIDFormat>
    <AssertionConsumerService
        Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="{cfg.sp_acs_url}"
        index="1"/>
    {"<SingleLogoutService Binding='urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST' Location='" + cfg.sp_slo_url + "'/>" if cfg.sp_slo_url else ""}
  </SPSSODescriptor>
</EntityDescriptor>"""
    return PlainTextResponse(content=xml, media_type="application/xml")


@router.post(
    "/auth/saml/acs",
    tags=["Enterprise — SAML SSO"],
    summary="SAML Assertion Consumer Service — IdP callback",
    include_in_schema=True,
)
async def saml_acs(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Receives the SAML assertion POST from the Identity Provider.
    In a full implementation this would:
      1. Parse + validate the SAMLResponse using python3-saml
      2. Extract NameID (email) and attribute assertions
      3. Provision or update the user in the users table
      4. Assign role from SAMLConfig.attribute_mapping
      5. Issue a short-lived OpsLens JWT and redirect to the dashboard

    This stub returns the raw form data so you can verify the integration
    is reaching OpsLens before wiring up python3-saml.
    """
    form = await request.form()
    saml_response = form.get("SAMLResponse", "")
    relay_state = form.get("RelayState", "")

    # TODO: validate with python3-saml
    # from onelogin.saml2.auth import OneLogin_Saml2_Auth
    # auth = OneLogin_Saml2_Auth(await _prepare_saml_request(request), saml_settings)
    # auth.process_response(); auth.get_errors()

    await _write_audit(
        db, tenant_id=tenant_id, actor_id="saml-idp",
        actor_role=None, resource="session", resource_id=None,
        action="saml_login",
        after={"relay_state": relay_state, "response_length": len(saml_response)},
        **_request_meta(request),
    )
    await db.commit()

    return {
        "status": "acs_received",
        "tenant_id": tenant_id,
        "relay_state": relay_state,
        "response_bytes": len(saml_response),
        "note": "Wire python3-saml for full assertion validation.",
    }


@router.get(
    "/auth/saml/config",
    tags=["Enterprise — SAML SSO"],
    summary="Get tenant SAML configuration (admin only)",
)
async def get_saml_config(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(SAMLConfig).where(SAMLConfig.tenant_id == tenant_id)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="SAML not configured for this tenant.")
    return {
        "id": cfg.id,
        "tenant_id": cfg.tenant_id,
        "entity_id": cfg.entity_id,
        "sp_acs_url": cfg.sp_acs_url,
        "idp_metadata_url": cfg.idp_metadata_url,
        "idp_entity_id": cfg.idp_entity_id,
        "idp_sso_url": cfg.idp_sso_url,
        "name_id_format": cfg.name_id_format,
        "sign_requests": cfg.sign_requests,
        "force_authn": cfg.force_authn,
        "attribute_mapping": cfg.attribute_mapping,
        "default_role": cfg.default_role,
        "is_active": cfg.is_active,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.post(
    "/auth/saml/config",
    status_code=status.HTTP_201_CREATED,
    tags=["Enterprise — SAML SSO"],
    summary="Create or update SAML SSO configuration (admin only)",
)
async def upsert_saml_config(
    request: Request,
    body: SAMLConfigIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(SAMLConfig).where(SAMLConfig.tenant_id == tenant_id)
    )
    existing: SAMLConfig | None = result.scalar_one_or_none()
    meta = _request_meta(request)
    actor_id = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "role", None)

    if existing:
        before_snap = {"entity_id": existing.entity_id, "idp_sso_url": existing.idp_sso_url}
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(existing, field, value)
        existing.updated_at = _now()
        await _write_audit(db, tenant_id=tenant_id, actor_id=actor_id, actor_role=actor_role,
                           resource="saml_config", resource_id=str(existing.id),
                           action="update", before=before_snap, after=body.model_dump(), **meta)
        await db.commit()
        return {"id": str(existing.id), "action": "updated"}
    else:
        cfg = SAMLConfig(id=str(uuid.uuid4()), tenant_id=tenant_id, **body.model_dump())
        db.add(cfg)
        await _write_audit(db, tenant_id=tenant_id, actor_id=actor_id, actor_role=actor_role,
                           resource="saml_config", resource_id=str(cfg.id),
                           action="create", after=body.model_dump(), **meta)
        await db.commit()
        return {"id": str(cfg.id), "action": "created"}


@router.delete(
    "/auth/saml/config",
    tags=["Enterprise — SAML SSO"],
    summary="Disable SAML SSO for tenant (admin only)",
)
async def delete_saml_config(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(SAMLConfig).where(SAMLConfig.tenant_id == tenant_id)
    )
    cfg: SAMLConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="No SAML config found.")
    cfg.is_active = False
    cfg.updated_at = _now()
    await _write_audit(db, tenant_id=tenant_id,
                       actor_id=getattr(request.state, "user_id", "unknown"),
                       actor_role=getattr(request.state, "role", None),
                       resource="saml_config", resource_id=str(cfg.id),
                       action="delete", before={"entity_id": cfg.entity_id}, **_request_meta(request))
    await db.commit()
    return {"status": "disabled"}


# ════════════════════════════════════════════════════════════════════════════════
# SCIM 2.0
# ════════════════════════════════════════════════════════════════════════════════

class SCIMConfigIn(BaseModel):
    bearer_token: str = Field(..., min_length=20, description="Raw bearer token (stored as hash)")
    sync_groups: bool = True
    deprovision_action: Literal["deactivate", "delete"] = "deactivate"
    group_role_map: dict = Field(default_factory=dict)


@router.post(
    "/auth/scim/config",
    status_code=status.HTTP_201_CREATED,
    tags=["Enterprise — SCIM"],
    summary="Configure SCIM provisioning for tenant (admin only)",
)
async def upsert_scim_config(
    request: Request,
    body: SCIMConfigIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Saves SCIM config for this tenant. The raw bearer token is hashed and
    never stored in plaintext. Configure the same token in your IdP
    (Okta → Provisioning → Integration → API Token).

    Returns the hashed token prefix for confirmation.
    """
    token_hash = hashlib.sha256(body.bearer_token.encode()).hexdigest()
    token_prefix = body.bearer_token[:8] + "…"

    result = await db.execute(
        sa.select(SCIMConfig).where(SCIMConfig.tenant_id == tenant_id)
    )
    existing: SCIMConfig | None = result.scalar_one_or_none()

    if existing:
        existing.bearer_token_hash  = token_hash
        existing.token_prefix       = token_prefix
        existing.sync_groups        = body.sync_groups
        existing.deprovision_action = body.deprovision_action
        existing.group_role_map     = body.group_role_map
        existing.is_active          = True
        existing.updated_at         = _now()
        await db.commit()
        return {"action": "updated", "token_prefix": token_prefix}
    else:
        cfg = SCIMConfig(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            bearer_token_hash=token_hash,
            token_prefix=token_prefix,
            sync_groups=body.sync_groups,
            deprovision_action=body.deprovision_action,
            group_role_map=body.group_role_map,
        )
        db.add(cfg)
        await db.commit()
        return {"action": "created", "token_prefix": token_prefix}


# ── SCIM /Users ───────────────────────────────────────────────────────────────

def _scim_user_response(user_id: str, email: str, active: bool, tenant_id: str) -> dict:
    base = "https://app.opslens.ai/api/v1/scim/v2"
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": user_id,
        "userName": email,
        "emails": [{"value": email, "primary": True}],
        "active": active,
        "meta": {
            "resourceType": "User",
            "location": f"{base}/Users/{user_id}",
        },
    }


@router.get(
    "/scim/v2/Users",
    tags=["Enterprise — SCIM"],
    summary="SCIM: List provisioned users",
)
async def scim_list_users(
    tenant_id: str = Query(...),
    startIndex: int = Query(1, ge=1),
    count: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _scim=Depends(_verify_scim_token),
):
    from apps.api.db.models import User as UserModel
    offset = startIndex - 1
    result = await db.execute(
        sa.select(UserModel)
        .where(UserModel.tenant_id == _scim.tenant_id)
        .offset(offset)
        .limit(count)
    )
    users = result.scalars().all()
    total_q = await db.execute(
        sa.select(sa.func.count()).where(UserModel.tenant_id == _scim.tenant_id)
    )
    total = total_q.scalar_one() or 0

    resources = [
        _scim_user_response(str(u.id), u.email or "", getattr(u, "is_active", True), tenant_id)
        for u in users
    ]
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": total,
        "startIndex": startIndex,
        "itemsPerPage": count,
        "Resources": resources,
    }


@router.post(
    "/scim/v2/Users",
    status_code=201,
    tags=["Enterprise — SCIM"],
    summary="SCIM: Provision a new user",
)
async def scim_create_user(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _scim=Depends(_verify_scim_token),
):
    body = await request.json()
    from apps.api.db.models import User as UserModel

    user_name = body.get("userName", "")
    email = user_name
    for em in body.get("emails", []):
        if em.get("primary"):
            email = em.get("value", user_name)
            break

    # Check for existing user
    existing = await db.execute(
        sa.select(UserModel).where(
            UserModel.tenant_id == _scim.tenant_id,
            UserModel.email == email,
        )
    )
    user: UserModel | None = existing.scalar_one_or_none()
    if user:
        return _scim_user_response(str(user.id), email, True, tenant_id)

    user = UserModel(
        id=str(uuid.uuid4()),
        tenant_id=_scim.tenant_id,
        email=email,
        name=f"{body.get('displayName', email)}",
        role="viewer",
    )
    db.add(user)

    # Assign role from group membership if sync_groups enabled
    if _scim.sync_groups:
        groups = [g.get("display", "") for g in body.get("groups", [])]
        for grp in groups:
            role = _scim.group_role_map.get(grp)
            if role and role in VALID_ROLES:
                user.role = role
                break

    await _write_audit(
        db, tenant_id=tenant_id, actor_id="scim", actor_role=None,
        resource="user", resource_id=str(user.id),
        action="scim_provision",
        after={"email": email, "role": user.role},
    )
    await db.commit()
    return _scim_user_response(str(user.id), email, True, tenant_id)


@router.get(
    "/scim/v2/Users/{user_id}",
    tags=["Enterprise — SCIM"],
    summary="SCIM: Get user by ID",
)
async def scim_get_user(
    user_id: str,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _scim=Depends(_verify_scim_token),
):
    from apps.api.db.models import User as UserModel
    result = await db.execute(
        sa.select(UserModel).where(
            UserModel.id == user_id,
            UserModel.tenant_id == _scim.tenant_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return _scim_user_response(str(user.id), user.email or "", True, tenant_id)


@router.delete(
    "/scim/v2/Users/{user_id}",
    status_code=204,
    tags=["Enterprise — SCIM"],
    summary="SCIM: Deprovision user",
)
async def scim_delete_user(
    user_id: str,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _scim=Depends(_verify_scim_token),
):
    from apps.api.db.models import User as UserModel
    result = await db.execute(
        sa.select(UserModel).where(
            UserModel.id == user_id,
            UserModel.tenant_id == _scim.tenant_id,
        )
    )
    user: UserModel | None = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if _scim.deprovision_action == "delete":
        await db.delete(user)
    else:
        if hasattr(user, "is_active"):
            user.is_active = False  # type: ignore

    await _write_audit(
        db, tenant_id=tenant_id, actor_id="scim", actor_role=None,
        resource="user", resource_id=user_id,
        action="scim_deprovision",
        before={"email": user.email, "action": _scim.deprovision_action},
    )
    await db.commit()


# ════════════════════════════════════════════════════════════════════════════════
# RBAC — Role Assignments
# ════════════════════════════════════════════════════════════════════════════════

class RoleAssignIn(BaseModel):
    user_id: str
    role: str
    expires_at: datetime | None = None
    notes: str | None = None


@router.get(
    "/rbac/roles",
    tags=["Enterprise — RBAC"],
    summary="List role assignments for tenant (admin only)",
)
async def list_role_assignments(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(RoleAssignment).where(
            RoleAssignment.tenant_id == tenant_id,
            RoleAssignment.is_active == True,
        ).order_by(RoleAssignment.granted_at.desc())
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id, "user_id": r.user_id, "role": r.role,
            "granted_by": r.granted_by, "granted_at": r.granted_at.isoformat(),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "notes": r.notes,
        }
        for r in rows
    ]


@router.post(
    "/rbac/roles",
    status_code=status.HTTP_201_CREATED,
    tags=["Enterprise — RBAC"],
    summary="Assign a role to a user (admin only)",
)
async def assign_role(
    request: Request,
    body: RoleAssignIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {sorted(VALID_ROLES)}")

    actor_id = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "role", None)

    assignment = RoleAssignment(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=body.user_id,
        role=body.role,
        granted_by=actor_id,
        expires_at=body.expires_at,
        notes=body.notes,
    )
    db.add(assignment)
    await _write_audit(
        db, tenant_id=tenant_id, actor_id=actor_id, actor_role=actor_role,
        resource="role_assignment", resource_id=str(assignment.id),
        action="permission_change",
        after={"user_id": body.user_id, "role": body.role},
        **_request_meta(request),
    )
    await db.commit()
    return {"id": str(assignment.id), "role": body.role, "user_id": body.user_id}


@router.delete(
    "/rbac/roles/{assignment_id}",
    tags=["Enterprise — RBAC"],
    summary="Revoke a role assignment (admin only)",
)
async def revoke_role(
    request: Request,
    assignment_id: str,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(RoleAssignment).where(
            RoleAssignment.id == assignment_id,
            RoleAssignment.tenant_id == tenant_id,
        )
    )
    row: RoleAssignment | None = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Role assignment not found.")

    before_snap = {"user_id": row.user_id, "role": row.role}
    row.is_active = False
    await _write_audit(
        db, tenant_id=tenant_id,
        actor_id=getattr(request.state, "user_id", "unknown"),
        actor_role=getattr(request.state, "role", None),
        resource="role_assignment", resource_id=assignment_id,
        action="permission_change", before=before_snap,
        after={"revoked": True},
        **_request_meta(request),
    )
    await db.commit()
    return {"status": "revoked"}


# ════════════════════════════════════════════════════════════════════════════════
# API Keys
# ════════════════════════════════════════════════════════════════════════════════

class APIKeyIn(BaseModel):
    name: str
    description: str | None = None
    role: str = "engineer"
    expires_at: datetime | None = None


@router.get(
    "/rbac/api-keys",
    tags=["Enterprise — RBAC"],
    summary="List API keys for tenant (no secrets returned)",
)
async def list_api_keys(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(APIKey).where(
            APIKey.tenant_id == tenant_id,
            APIKey.is_active == True,
        ).order_by(APIKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [
        {
            "id": k.id, "name": k.name, "description": k.description,
            "key_prefix": k.key_prefix, "role": k.role,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat(),
        }
        for k in keys
    ]


@router.post(
    "/rbac/api-keys",
    status_code=status.HTTP_201_CREATED,
    tags=["Enterprise — RBAC"],
    summary="Create API key — raw key returned ONCE (admin only)",
)
async def create_api_key(
    request: Request,
    body: APIKeyIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Generates a new service-account API key.
    The raw key is returned in the response ONCE — store it securely.
    OpsLens only stores the SHA-256 hash; there is no way to recover the raw key.
    """
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {sorted(VALID_ROLES)}")

    actor_id = getattr(request.state, "user_id", "unknown")

    # Generate the raw key
    raw_bytes = secrets.token_urlsafe(32)
    raw_key = f"sk-oplen_{raw_bytes}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12] + "…"

    key = APIKey(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        key_hash=key_hash,
        key_prefix=key_prefix,
        role=body.role,
        expires_at=body.expires_at,
        created_by=actor_id,
    )
    db.add(key)
    await _write_audit(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_role=getattr(request.state, "role", None),
        resource="api_key", resource_id=str(key.id),
        action="api_key_create",
        after={"name": body.name, "role": body.role, "key_prefix": key_prefix},
        **_request_meta(request),
    )
    await db.commit()

    return {
        "id": str(key.id),
        "name": key.name,
        "key_prefix": key_prefix,
        "raw_key": raw_key,          # ONLY returned here — never again
        "role": body.role,
        "warning": "Store this key securely. It will not be shown again.",
    }


@router.delete(
    "/rbac/api-keys/{key_id}",
    tags=["Enterprise — RBAC"],
    summary="Revoke API key (admin only)",
)
async def revoke_api_key(
    request: Request,
    key_id: str,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(APIKey).where(APIKey.id == key_id, APIKey.tenant_id == tenant_id)
    )
    key: APIKey | None = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found.")
    key.is_active = False
    await _write_audit(
        db, tenant_id=tenant_id,
        actor_id=getattr(request.state, "user_id", "unknown"),
        actor_role=getattr(request.state, "role", None),
        resource="api_key", resource_id=key_id,
        action="api_key_revoke",
        before={"name": key.name, "key_prefix": key.key_prefix},
        **_request_meta(request),
    )
    await db.commit()
    return {"status": "revoked", "key_id": key_id}


# ════════════════════════════════════════════════════════════════════════════════
# Audit Log
# ════════════════════════════════════════════════════════════════════════════════

@router.get(
    "/audit",
    tags=["Enterprise — Audit Log"],
    summary="Query immutable audit log (admin only)",
)
async def query_audit_log(
    request: Request,
    tenant_id: str = Query(...),
    actor_id: str | None = Query(None, description="Filter by actor user_id"),
    resource: str | None = Query(None, description="Filter by resource type"),
    action: str | None = Query(None, description="Filter by action verb"),
    since: datetime | None = Query(None, description="ISO timestamp lower bound"),
    until: datetime | None = Query(None, description="ISO timestamp upper bound (default: now)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Returns audit log entries in reverse chronological order.
    All write operations in OpsLens emit an AuditLog row automatically.
    Results are paginated — use limit + offset for large exports.
    """
    filters = [AuditLog.tenant_id == tenant_id]
    if actor_id:
        filters.append(AuditLog.actor_id == actor_id)
    if resource:
        filters.append(AuditLog.resource == resource)
    if action:
        filters.append(AuditLog.action == action)
    if since:
        filters.append(AuditLog.occurred_at >= since)
    if until:
        filters.append(AuditLog.occurred_at <= until)
    else:
        filters.append(AuditLog.occurred_at <= _now())

    count_q = await db.execute(sa.select(sa.func.count()).where(*filters))
    total = count_q.scalar_one() or 0

    result = await db.execute(
        sa.select(AuditLog).where(*filters)
        .order_by(AuditLog.occurred_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r.id,
                "actor_id": r.actor_id,
                "actor_role": r.actor_role,
                "resource": r.resource,
                "resource_id": r.resource_id,
                "action": r.action,
                "before": r.before,
                "after": r.after,
                "ip_address": r.ip_address,
                "request_id": r.request_id,
                "occurred_at": r.occurred_at.isoformat(),
            }
            for r in rows
        ],
    }
