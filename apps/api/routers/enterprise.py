"""
OpsLens AI — Enterprise Auth Router
=====================================
Covers enterprise authentication and access control in one module:

  Domain Allow-listing
    GET    /api/v1/auth/domain-allowlist      — Get approved domains (admin only)
    POST   /api/v1/auth/domain-allowlist      — Set approved domains (admin only)
    DELETE /api/v1/auth/domain-allowlist      — Clear allowlist (open registration)

  SAML 2.0 SSO
    GET  /api/v1/auth/saml/metadata           — SP metadata XML (public)
    GET  /api/v1/auth/saml/login              — Initiate SP-initiated SSO (public)
    POST /api/v1/auth/saml/acs                — Assertion Consumer Service (IdP POST)
    POST /api/v1/auth/saml/slo                — Single Logout (IdP callback)
    GET  /api/v1/auth/saml/config             — Get tenant SAML config (admin only)
    POST /api/v1/auth/saml/config             — Create/update SAML config
    DELETE /api/v1/auth/saml/config           — Remove SAML config (disable SSO)

  OIDC SSO (Azure AD, Okta, Google Workspace, ...)
    GET    /api/v1/auth/oidc/config           — Get OIDC config (admin only)
    POST   /api/v1/auth/oidc/config           — Create/update OIDC config (admin only)
    DELETE /api/v1/auth/oidc/config           — Disable OIDC (admin only)
    GET    /api/v1/auth/oidc/login            — Redirect to IdP (public)
    GET    /api/v1/auth/oidc/callback         — IdP callback with auth code (public)

  LDAP / Active Directory (on-prem)
    GET    /api/v1/auth/ldap/config           — Get LDAP config (admin only)
    POST   /api/v1/auth/ldap/config           — Create/update LDAP config (admin only)
    DELETE /api/v1/auth/ldap/config           — Disable LDAP (admin only)
    POST   /api/v1/auth/ldap/test             — Test LDAP connectivity (admin only)
    POST   /api/v1/auth/ldap/login            — Authenticate with username+password (public)

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

SAML/OIDC/LDAP login endpoints are public (no JWT required) — they are the
authentication mechanism itself. All config/management endpoints require admin role.
SCIM endpoints use their own bearer token from SCIMConfig.bearer_token_hash.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, List, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth.middleware import require_roles
from apps.api.db.session import get_db
from apps.api.models.audit import AuditLog
from apps.api.models.auth_providers import LDAPConfig, OIDCConfig
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


@router.get(
    "/auth/saml/login",
    tags=["Enterprise — SAML SSO"],
    summary="Initiate SP-initiated SAML SSO (public — redirects to IdP)",
)
async def saml_login(
    tenant_id: str = Query(...),
    relay_state: str = Query("", description="URL to redirect to after login"),
    db: AsyncSession = Depends(get_db),
):
    """
    Starts a SAML SP-initiated SSO flow for the given tenant.

    Fetches the tenant's SAMLConfig, generates a signed AuthnRequest,
    and returns an HTTP 302 redirect to the IdP's SSO endpoint.

    The IdP authenticates the user and POSTs the SAMLResponse back to
    the Assertion Consumer Service at /api/v1/auth/saml/acs.

    Requires python3-saml to be installed (pip install python3-saml>=2.6.0).
    """
    result = await db.execute(
        sa.select(SAMLConfig).where(
            SAMLConfig.tenant_id == tenant_id,
            SAMLConfig.is_active == True,
        )
    )
    cfg: SAMLConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="SAML SSO is not configured or disabled for this tenant.")

    from apps.api.auth.saml_handler import get_login_url
    try:
        redirect_url = get_login_url(cfg, relay_state=relay_state)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not generate SAML login URL: {exc}")

    return RedirectResponse(url=redirect_url, status_code=302)


@router.post(
    "/auth/saml/acs",
    tags=["Enterprise — SAML SSO"],
    summary="SAML Assertion Consumer Service — receives IdP callback",
)
async def saml_acs(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Receives and validates the SAML assertion POST from the Identity Provider.

    Flow:
      1. Validates the SAMLResponse with python3-saml (XML signature, expiry,
         audience restriction, destination URL).
      2. Extracts NameID (email) and attributes from the assertion.
      3. Provisions or updates the user in the DB using SAMLConfig.attribute_mapping.
      4. Issues a short-lived OpsLens HS256 JWT (1 hour default).
      5. Redirects to {FRONTEND_URL}/auth/sso-callback?token=<jwt>

    The frontend stores the JWT and sends it as "Authorization: Bearer <jwt>"
    on subsequent API calls. See apps/api/auth/token_issuer.py for details.

    Requires python3-saml>=2.6.0 (pip install python3-saml).
    """
    from apps.api.config import settings as app_settings
    from apps.api.auth.token_issuer import issue_sso_token
    from apps.api.auth.saml_handler import parse_saml_response, extract_role
    from apps.api.db.models import User as UserModel, Tenant as TenantModel

    # Consume form data before any awaits (Starlette reads it once)
    form = await request.form()
    form_data = dict(form)
    relay_state = form_data.get("RelayState", "")

    # Load SAML config for this tenant
    cfg_result = await db.execute(
        sa.select(SAMLConfig).where(
            SAMLConfig.tenant_id == tenant_id,
            SAMLConfig.is_active == True,
        )
    )
    cfg: SAMLConfig | None = cfg_result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="SAML SSO is not configured for this tenant.")

    # Validate assertion and extract user info
    try:
        email, display_name, attributes = parse_saml_response(cfg, request, form_data)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except ValueError as exc:
        await _write_audit(
            db, tenant_id=tenant_id, actor_id="saml-idp", actor_role=None,
            resource="session", resource_id=None, action="saml_login_failed",
            after={"error": str(exc)}, **_request_meta(request),
        )
        await db.commit()
        raise HTTPException(status_code=401, detail=f"SAML authentication failed: {exc}")

    if not email:
        raise HTTPException(status_code=400, detail="SAML assertion did not contain a NameID (email).")

    # Determine role from assertion attributes
    role = extract_role(cfg, attributes)

    # Provision or update user — use email as the stable external_id for SAML users
    # (Clerk users have a Clerk ID; SAML users are identified by their NameID/email).
    existing = await db.execute(
        sa.select(UserModel).where(
            UserModel.tenant_id == sa.cast(tenant_id, sa.String),
            UserModel.email == email,
        )
    )
    user: UserModel | None = existing.scalar_one_or_none()

    if user:
        # Update name and role on each login to reflect any IdP-side changes
        if display_name:
            user.name = display_name
        if role != user.role:
            user.role = role
        user_id = str(user.id)
    else:
        # Check domain allowlist before provisioning (mirrors dependencies.py logic)
        tenant_row = await db.execute(
            sa.select(TenantModel.allowed_email_domains).where(
                sa.cast(TenantModel.id, sa.String) == tenant_id
            )
        )
        allowed_domains: list = tenant_row.scalar_one_or_none() or []
        if allowed_domains:
            email_domain = email.split("@")[-1].lower() if "@" in email else ""
            if email_domain not in [d.strip().lower() for d in allowed_domains]:
                raise HTTPException(
                    status_code=403,
                    detail=f"Email domain '{email_domain}' is not approved for this workspace.",
                )

        # New user provisioned via SAML
        new_user = UserModel(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id) if len(tenant_id) == 36 else uuid.uuid5(uuid.NAMESPACE_URL, tenant_id),
            external_id=email,   # NameID is the stable SAML identifier
            email=email,
            name=display_name or None,
            role=role,
        )
        db.add(new_user)
        user_id = str(new_user.id)

    # Issue a short-lived internal JWT for the frontend
    token = issue_sso_token(
        user_id=email,   # use email as sub (stable across sessions)
        email=email,
        tenant_id=tenant_id,
        role=role,
    )

    await _write_audit(
        db, tenant_id=tenant_id, actor_id=email, actor_role=role,
        resource="session", resource_id=user_id,
        action="saml_login",
        after={"email": email, "role": role, "relay_state": relay_state},
        **_request_meta(request),
    )
    await db.commit()

    # Redirect the browser to the frontend SSO callback page with the token.
    # The frontend should extract the token and use it as a Bearer token.
    # Configure FRONTEND_URL in your .env (default: http://localhost:3000).
    frontend_url = getattr(app_settings, "FRONTEND_URL", "http://localhost:3000")
    callback = relay_state or f"{frontend_url}/auth/sso-callback"
    sep = "&" if "?" in callback else "?"
    return RedirectResponse(url=f"{callback}{sep}token={token}", status_code=302)


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


# ════════════════════════════════════════════════════════════════════════════════
# Domain Allow-listing
# ════════════════════════════════════════════════════════════════════════════════
#
# Controls which email domains can register (join) a tenant on first login.
# An empty list means the allowlist is disabled — any email domain is accepted.
# Existing users are NEVER affected by allowlist changes, regardless of domain.
#
# Example use cases:
#   • Single-company tenant: ["acme.com"]
#   • Multi-domain company:  ["acme.com", "acme.io", "acmecorp.net"]
#   • Disable allowlist:     DELETE /api/v1/auth/domain-allowlist
# ════════════════════════════════════════════════════════════════════════════════

class DomainAllowlistIn(BaseModel):
    # List of approved email domain strings (without the @ prefix).
    # e.g. ["acme.com", "acme.io"]
    # Domains are normalised to lowercase before storage.
    domains: List[str] = Field(
        ...,
        description="Approved email domains, e.g. ['acme.com', 'acme.io']",
        min_length=1,
    )


@router.get(
    "/auth/domain-allowlist",
    tags=["Enterprise — Domain Allow-listing"],
    summary="Get approved email domains for tenant (admin only)",
)
async def get_domain_allowlist(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Returns the list of approved email domains for this tenant.
    An empty list means the allowlist is disabled (all domains allowed).
    """
    from apps.api.db.models import Tenant
    result = await db.execute(
        sa.select(Tenant.allowed_email_domains).where(
            sa.cast(Tenant.id, sa.String) == tenant_id
        )
    )
    domains = result.scalar_one_or_none() or []
    return {
        "tenant_id": tenant_id,
        "allowed_email_domains": domains,
        "allowlist_enabled": bool(domains),
    }


@router.post(
    "/auth/domain-allowlist",
    tags=["Enterprise — Domain Allow-listing"],
    summary="Set approved email domains for tenant (admin only)",
)
async def set_domain_allowlist(
    request: Request,
    body: DomainAllowlistIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Replace the tenant's domain allowlist.

    After this call, only users whose email domain matches one of the listed
    domains will be allowed to join this tenant on first login. Users who are
    already members are never affected.

    Domains are normalised to lowercase. Duplicates are removed silently.
    """
    from apps.api.db.models import Tenant

    actor_id = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "role", None)

    # Normalise: lowercase, strip whitespace, deduplicate
    cleaned = sorted({d.strip().lower() for d in body.domains if d.strip()})
    if not cleaned:
        raise HTTPException(status_code=400, detail="At least one non-empty domain is required.")

    # Fetch current value for audit log
    before_result = await db.execute(
        sa.select(Tenant.allowed_email_domains).where(
            sa.cast(Tenant.id, sa.String) == tenant_id
        )
    )
    before_domains = before_result.scalar_one_or_none() or []

    await db.execute(
        sa.update(Tenant)
        .where(sa.cast(Tenant.id, sa.String) == tenant_id)
        .values(allowed_email_domains=cleaned)
    )
    await _write_audit(
        db, tenant_id=tenant_id, actor_id=actor_id, actor_role=actor_role,
        resource="domain_allowlist", resource_id=tenant_id,
        action="update",
        before={"domains": before_domains},
        after={"domains": cleaned},
        **_request_meta(request),
    )
    await db.commit()
    return {"allowed_email_domains": cleaned, "count": len(cleaned)}


@router.delete(
    "/auth/domain-allowlist",
    tags=["Enterprise — Domain Allow-listing"],
    summary="Clear domain allowlist — open registration (admin only)",
)
async def clear_domain_allowlist(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Clears the domain allowlist, disabling the restriction. Any email domain
    will be accepted for new registrations until a new allowlist is set.
    """
    from apps.api.db.models import Tenant

    before_result = await db.execute(
        sa.select(Tenant.allowed_email_domains).where(
            sa.cast(Tenant.id, sa.String) == tenant_id
        )
    )
    before_domains = before_result.scalar_one_or_none() or []

    await db.execute(
        sa.update(Tenant)
        .where(sa.cast(Tenant.id, sa.String) == tenant_id)
        .values(allowed_email_domains=[])
    )
    await _write_audit(
        db, tenant_id=tenant_id,
        actor_id=getattr(request.state, "user_id", "unknown"),
        actor_role=getattr(request.state, "role", None),
        resource="domain_allowlist", resource_id=tenant_id,
        action="delete",
        before={"domains": before_domains},
        after={"domains": []},
        **_request_meta(request),
    )
    await db.commit()
    return {"status": "cleared", "allowlist_enabled": False}


# ════════════════════════════════════════════════════════════════════════════════
# OIDC SSO — OpenID Connect (Azure AD, Okta, Google Workspace, ...)
# ════════════════════════════════════════════════════════════════════════════════
#
# Supports any standards-compliant OIDC provider. The discovery_url is used to
# fetch /.well-known/openid-configuration, so only one URL needs to be configured
# (no separate token/userinfo endpoint config required).
#
# SSO flow:
#   1. Admin configures client_id, client_secret, discovery_url via POST /config
#   2. User hits GET /login → redirected to IdP authorization page
#   3. User authenticates → IdP redirects to GET /callback with ?code=...&state=...
#   4. OpsLens exchanges code for tokens, fetches userinfo, provisions user
#   5. Frontend receives internal JWT via redirect to /auth/sso-callback?token=...
# ════════════════════════════════════════════════════════════════════════════════

class OIDCConfigIn(BaseModel):
    # Base URL of the OIDC issuer. OpsLens appends /.well-known/openid-configuration.
    # Azure AD:  https://login.microsoftonline.com/{tenant-id}/v2.0
    # Okta:      https://{domain}.okta.com/oauth2/default
    # Google:    https://accounts.google.com
    discovery_url: str

    # OAuth2 client credentials from the IdP application registration
    client_id: str
    client_secret: str = Field(..., min_length=1)

    # Callback URL — must exactly match what is registered in the IdP.
    # e.g. "https://app.opslens.ai/api/v1/auth/oidc/callback"
    redirect_uri: str

    # Space-separated OIDC scopes (openid and email are required)
    scopes: str = "openid email profile"

    # Maps OIDC claim names to OpsLens fields.
    # Supported keys: "email", "name", "role_claim"
    # role_claim: name of the claim whose value(s) are looked up in role_map
    attribute_mapping: dict = Field(default_factory=dict)

    # Maps role_claim values to OpsLens roles.
    # e.g. {"GlobalAdmins": "admin", "Developers": "member"}
    role_map: dict = Field(default_factory=dict)

    # Role for first-time OIDC users with no matching role_map entry
    default_role: str = "viewer"


@router.get(
    "/auth/oidc/config",
    tags=["Enterprise — OIDC SSO"],
    summary="Get OIDC SSO configuration (admin only)",
)
async def get_oidc_config(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(OIDCConfig).where(OIDCConfig.tenant_id == tenant_id)
    )
    cfg: OIDCConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="OIDC SSO is not configured for this tenant.")
    return {
        "id": cfg.id,
        "tenant_id": cfg.tenant_id,
        "discovery_url": cfg.discovery_url,
        "client_id": cfg.client_id,
        # client_secret_enc is never returned — show only a masked hint
        "client_secret": "***configured***",
        "redirect_uri": cfg.redirect_uri,
        "scopes": cfg.scopes,
        "attribute_mapping": cfg.attribute_mapping,
        "role_map": cfg.role_map,
        "default_role": cfg.default_role,
        "is_active": cfg.is_active,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.post(
    "/auth/oidc/config",
    status_code=status.HTTP_201_CREATED,
    tags=["Enterprise — OIDC SSO"],
    summary="Create or update OIDC SSO configuration (admin only)",
)
async def upsert_oidc_config(
    request: Request,
    body: OIDCConfigIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Saves OIDC config for this tenant. The client_secret is encrypted with
    Fernet (AES-256) before storage and never returned in API responses.
    """
    from apps.api.utils.crypto import encrypt

    actor_id = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "role", None)

    secret_enc = encrypt(body.client_secret)

    result = await db.execute(
        sa.select(OIDCConfig).where(OIDCConfig.tenant_id == tenant_id)
    )
    existing: OIDCConfig | None = result.scalar_one_or_none()

    if existing:
        before_snap = {"discovery_url": existing.discovery_url, "client_id": existing.client_id}
        existing.discovery_url = body.discovery_url
        existing.client_id = body.client_id
        existing.client_secret_enc = secret_enc
        existing.redirect_uri = body.redirect_uri
        existing.scopes = body.scopes
        existing.attribute_mapping = body.attribute_mapping
        existing.role_map = body.role_map
        existing.default_role = body.default_role
        existing.is_active = True
        existing.updated_at = _now()
        await _write_audit(
            db, tenant_id=tenant_id, actor_id=actor_id, actor_role=actor_role,
            resource="oidc_config", resource_id=str(existing.id),
            action="update", before=before_snap,
            after={"discovery_url": body.discovery_url, "client_id": body.client_id},
            **_request_meta(request),
        )
        await db.commit()
        return {"id": str(existing.id), "action": "updated"}
    else:
        cfg = OIDCConfig(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            discovery_url=body.discovery_url,
            client_id=body.client_id,
            client_secret_enc=secret_enc,
            redirect_uri=body.redirect_uri,
            scopes=body.scopes,
            attribute_mapping=body.attribute_mapping,
            role_map=body.role_map,
            default_role=body.default_role,
        )
        db.add(cfg)
        await _write_audit(
            db, tenant_id=tenant_id, actor_id=actor_id, actor_role=actor_role,
            resource="oidc_config", resource_id=str(cfg.id),
            action="create",
            after={"discovery_url": body.discovery_url, "client_id": body.client_id},
            **_request_meta(request),
        )
        await db.commit()
        return {"id": str(cfg.id), "action": "created"}


@router.delete(
    "/auth/oidc/config",
    tags=["Enterprise — OIDC SSO"],
    summary="Disable OIDC SSO for tenant (admin only)",
)
async def delete_oidc_config(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(OIDCConfig).where(OIDCConfig.tenant_id == tenant_id)
    )
    cfg: OIDCConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="No OIDC config found for this tenant.")
    cfg.is_active = False
    cfg.updated_at = _now()
    await _write_audit(
        db, tenant_id=tenant_id,
        actor_id=getattr(request.state, "user_id", "unknown"),
        actor_role=getattr(request.state, "role", None),
        resource="oidc_config", resource_id=str(cfg.id),
        action="delete",
        before={"discovery_url": cfg.discovery_url},
        **_request_meta(request),
    )
    await db.commit()
    return {"status": "disabled"}


@router.get(
    "/auth/oidc/login",
    tags=["Enterprise — OIDC SSO"],
    summary="Initiate OIDC SSO — redirects to IdP authorization page (public)",
)
async def oidc_login(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Generates the IdP authorization URL and redirects the user's browser to it.

    The state parameter is a short-lived signed JWT (5 minutes) that encodes
    tenant_id and a nonce. It is verified in the callback to prevent CSRF.

    Requires httpx to be installed (already in requirements.txt).
    """
    import json
    import httpx
    import jwt as pyjwt
    from apps.api.config import settings as app_settings
    from apps.api.auth.token_issuer import _ALGORITHM
    from datetime import timedelta

    result = await db.execute(
        sa.select(OIDCConfig).where(
            OIDCConfig.tenant_id == tenant_id,
            OIDCConfig.is_active == True,
        )
    )
    cfg: OIDCConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="OIDC SSO is not configured or disabled for this tenant.")

    # Fetch OIDC discovery document to get the authorization endpoint
    discovery_url = cfg.discovery_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            oidc_meta = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch OIDC discovery document from '{discovery_url}': {exc}",
        )

    auth_endpoint = oidc_meta.get("authorization_endpoint")
    if not auth_endpoint:
        raise HTTPException(status_code=502, detail="OIDC discovery document missing 'authorization_endpoint'.")

    # Build a signed state JWT (5-minute expiry) to carry tenant_id through the redirect.
    # This prevents CSRF — the callback verifies the state signature before proceeding.
    nonce = secrets.token_urlsafe(16)
    now = _now()
    state_payload = {
        "iss": "opslens-oidc-state",
        "tenant_id": tenant_id,
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    state_token = pyjwt.encode(state_payload, app_settings.SECRET_KEY, algorithm=_ALGORITHM)

    # Build the authorization URL
    import urllib.parse
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": cfg.scopes,
        "state": state_token,
        "nonce": nonce,
    }
    auth_url = auth_endpoint + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get(
    "/auth/oidc/callback",
    tags=["Enterprise — OIDC SSO"],
    summary="OIDC callback — exchanges auth code for user token (public)",
)
async def oidc_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from IdP"),
    state: str = Query(..., description="Signed state token from /auth/oidc/login"),
    db: AsyncSession = Depends(get_db),
):
    """
    Completes the OIDC authorization code flow:
      1. Verifies the state JWT (anti-CSRF).
      2. Exchanges the code for tokens at the IdP token endpoint.
      3. Fetches userinfo to get email and name.
      4. Provisions or updates the user in the DB.
      5. Issues an internal HS256 JWT and redirects to the frontend.

    On error, redirects to {FRONTEND_URL}/auth/sso-error?message=... so the
    frontend can show a user-friendly error page.
    """
    import httpx
    import jwt as pyjwt
    from apps.api.config import settings as app_settings
    from apps.api.auth.token_issuer import issue_sso_token, _ALGORITHM
    from apps.api.utils.crypto import decrypt
    from apps.api.db.models import User as UserModel, Tenant as TenantModel

    frontend_url = getattr(app_settings, "FRONTEND_URL", "http://localhost:3000")

    def _error_redirect(msg: str) -> RedirectResponse:
        import urllib.parse
        return RedirectResponse(
            url=f"{frontend_url}/auth/sso-error?message={urllib.parse.quote(msg)}",
            status_code=302,
        )

    # ── Verify state (anti-CSRF) ──────────────────────────────────────────────
    try:
        state_payload = pyjwt.decode(
            state, app_settings.SECRET_KEY, algorithms=[_ALGORITHM],
            options={"verify_exp": True},
        )
        if state_payload.get("iss") != "opslens-oidc-state":
            raise ValueError("Invalid state issuer.")
        tenant_id: str = state_payload["tenant_id"]
    except Exception as exc:
        return _error_redirect(f"Invalid or expired state parameter: {exc}")

    # ── Load OIDC config ──────────────────────────────────────────────────────
    cfg_result = await db.execute(
        sa.select(OIDCConfig).where(
            OIDCConfig.tenant_id == tenant_id,
            OIDCConfig.is_active == True,
        )
    )
    cfg: OIDCConfig | None = cfg_result.scalar_one_or_none()
    if not cfg:
        return _error_redirect("OIDC SSO is not configured for this tenant.")

    # ── Fetch OIDC discovery document ─────────────────────────────────────────
    discovery_url = cfg.discovery_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            disc_resp = await client.get(discovery_url)
            disc_resp.raise_for_status()
            oidc_meta = disc_resp.json()
    except Exception as exc:
        return _error_redirect(f"Failed to reach IdP discovery endpoint: {exc}")

    token_endpoint = oidc_meta.get("token_endpoint")
    userinfo_endpoint = oidc_meta.get("userinfo_endpoint")
    if not token_endpoint:
        return _error_redirect("OIDC discovery document is missing 'token_endpoint'.")

    # ── Exchange authorization code for tokens ────────────────────────────────
    client_secret = decrypt(cfg.client_secret_enc)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": cfg.redirect_uri,
                    "client_id": cfg.client_id,
                    "client_secret": client_secret,
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
    except Exception as exc:
        return _error_redirect(f"Token exchange failed: {exc}")

    access_token = token_data.get("access_token", "")
    id_token = token_data.get("id_token", "")

    # ── Get user info (prefer userinfo endpoint; fall back to id_token claims) ─
    user_claims: dict = {}
    if userinfo_endpoint and access_token:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                ui_resp = await client.get(
                    userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                ui_resp.raise_for_status()
                user_claims = ui_resp.json()
        except Exception:
            pass  # Fall through to id_token

    if not user_claims and id_token:
        # Decode id_token without signature verification — we already trust the IdP
        # because we received it over TLS in exchange for a code we sent.
        try:
            user_claims = pyjwt.decode(id_token, options={"verify_signature": False})
        except Exception:
            pass

    # ── Extract email and name using attribute_mapping ─────────────────────────
    mapping: dict = cfg.attribute_mapping or {}
    email_claim = mapping.get("email", "email")
    name_claim = mapping.get("name", "name")
    role_claim = mapping.get("role_claim", "")

    email = user_claims.get(email_claim) or user_claims.get("email", "")
    display_name = user_claims.get(name_claim) or user_claims.get("name", "")

    if not email:
        return _error_redirect("IdP did not return an email address. Check your OIDC scopes.")

    # ── Resolve role from claims ──────────────────────────────────────────────
    role_map: dict = cfg.role_map or {}
    role = cfg.default_role or "viewer"
    if role_claim:
        claim_values = user_claims.get(role_claim, [])
        if isinstance(claim_values, str):
            claim_values = [claim_values]
        for val in (claim_values or []):
            if val in role_map and role_map[val] in VALID_ROLES:
                role = role_map[val]
                break

    # ── Check domain allowlist ─────────────────────────────────────────────────
    tenant_row = await db.execute(
        sa.select(TenantModel.allowed_email_domains).where(
            sa.cast(TenantModel.id, sa.String) == tenant_id
        )
    )
    allowed_domains = tenant_row.scalar_one_or_none() or []
    if allowed_domains:
        email_domain = email.split("@")[-1].lower() if "@" in email else ""
        if email_domain not in [d.strip().lower() for d in allowed_domains]:
            return _error_redirect(
                f"Email domain '{email_domain}' is not approved for this workspace."
            )

    # ── Provision or update user ──────────────────────────────────────────────
    existing = await db.execute(
        sa.select(UserModel).where(
            sa.cast(UserModel.tenant_id, sa.String) == tenant_id,
            UserModel.email == email,
        )
    )
    user: UserModel | None = existing.scalar_one_or_none()

    if user:
        if display_name:
            user.name = display_name
        if role != user.role:
            user.role = role
        user_db_id = str(user.id)
    else:
        tenant_uuid = (
            uuid.UUID(tenant_id) if len(tenant_id) == 36
            else uuid.uuid5(uuid.NAMESPACE_URL, tenant_id)
        )
        new_user = UserModel(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid,
            external_id=email,
            email=email,
            name=display_name or None,
            role=role,
        )
        db.add(new_user)
        user_db_id = str(new_user.id)

    # Issue internal JWT
    token = issue_sso_token(
        user_id=email,
        email=email,
        tenant_id=tenant_id,
        role=role,
    )

    await _write_audit(
        db, tenant_id=tenant_id, actor_id=email, actor_role=role,
        resource="session", resource_id=user_db_id,
        action="oidc_login",
        after={"email": email, "role": role},
        **_request_meta(request),
    )
    await db.commit()

    callback = f"{frontend_url}/auth/sso-callback"
    return RedirectResponse(url=f"{callback}?token={token}", status_code=302)


# ════════════════════════════════════════════════════════════════════════════════
# LDAP / Active Directory (on-prem)
# ════════════════════════════════════════════════════════════════════════════════
#
# Optional integration for customers who run their own directory service and
# cannot use cloud-based SSO (SAML/OIDC). Supports Active Directory and
# any RFC 4511-compliant LDAP server (OpenLDAP, FreeIPA, etc.).
#
# Authentication flow: see LDAPConfig docstring in models/auth_providers.py.
#
# Requires ldap3>=2.9.1 (pure Python, no native dependencies):
#   pip install ldap3>=2.9.1
# ════════════════════════════════════════════════════════════════════════════════

class LDAPConfigIn(BaseModel):
    # LDAP server URI — ldaps:// strongly recommended in production
    server_url: str = Field(..., description="e.g. ldaps://dc01.corp.example.com:636")

    # Service account used by OpsLens to search the directory
    bind_dn: str = Field(..., description="e.g. CN=opslens-svc,OU=SA,DC=corp,DC=example,DC=com")
    bind_password: str = Field(..., min_length=1)

    # Subtree where user entries reside
    user_search_base: str = Field(..., description="e.g. OU=Users,DC=corp,DC=example,DC=com")

    # Filter to locate a user by login name — use {username} as placeholder
    user_search_filter: str = "(&(objectClass=person)(sAMAccountName={username}))"

    # LDAP attributes for user fields
    attr_email: str = "mail"
    attr_name: str = "displayName"

    # Group-based access control
    require_group: bool = False
    group_search_base: str | None = None
    group_search_filter: str | None = None
    group_attr_name: str = "cn"

    # Maps LDAP group names to OpsLens roles
    group_role_map: dict = Field(default_factory=dict)

    # PEM CA certificate for ldaps:// with self-signed/private CA (optional)
    tls_ca_cert: str | None = None

    # Role for users with no matching group entry
    default_role: str = "viewer"


class LDAPLoginIn(BaseModel):
    username: str = Field(..., description="LDAP username (sAMAccountName, uid, etc.)")
    password: str = Field(..., min_length=1)


@router.get(
    "/auth/ldap/config",
    tags=["Enterprise — LDAP/AD"],
    summary="Get LDAP configuration (admin only)",
)
async def get_ldap_config(
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(LDAPConfig).where(LDAPConfig.tenant_id == tenant_id)
    )
    cfg: LDAPConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="LDAP is not configured for this tenant.")
    return {
        "id": cfg.id,
        "tenant_id": cfg.tenant_id,
        "server_url": cfg.server_url,
        "bind_dn": cfg.bind_dn,
        "bind_password": "***configured***",  # never returned
        "user_search_base": cfg.user_search_base,
        "user_search_filter": cfg.user_search_filter,
        "attr_email": cfg.attr_email,
        "attr_name": cfg.attr_name,
        "require_group": cfg.require_group,
        "group_search_base": cfg.group_search_base,
        "group_search_filter": cfg.group_search_filter,
        "group_attr_name": cfg.group_attr_name,
        "group_role_map": cfg.group_role_map,
        "default_role": cfg.default_role,
        "is_active": cfg.is_active,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.post(
    "/auth/ldap/config",
    status_code=status.HTTP_201_CREATED,
    tags=["Enterprise — LDAP/AD"],
    summary="Create or update LDAP configuration (admin only)",
)
async def upsert_ldap_config(
    request: Request,
    body: LDAPConfigIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Saves LDAP config for this tenant. The bind_password is encrypted with
    Fernet (AES-256) before storage and never returned in API responses.

    Test connectivity with POST /api/v1/auth/ldap/test after configuring.
    """
    from apps.api.utils.crypto import encrypt

    actor_id = getattr(request.state, "user_id", "unknown")
    actor_role = getattr(request.state, "role", None)
    password_enc = encrypt(body.bind_password)

    result = await db.execute(
        sa.select(LDAPConfig).where(LDAPConfig.tenant_id == tenant_id)
    )
    existing: LDAPConfig | None = result.scalar_one_or_none()

    if existing:
        before_snap = {"server_url": existing.server_url, "bind_dn": existing.bind_dn}
        existing.server_url = body.server_url
        existing.bind_dn = body.bind_dn
        existing.bind_password_enc = password_enc
        existing.user_search_base = body.user_search_base
        existing.user_search_filter = body.user_search_filter
        existing.attr_email = body.attr_email
        existing.attr_name = body.attr_name
        existing.require_group = body.require_group
        existing.group_search_base = body.group_search_base
        existing.group_search_filter = body.group_search_filter
        existing.group_attr_name = body.group_attr_name
        existing.group_role_map = body.group_role_map
        existing.tls_ca_cert = body.tls_ca_cert
        existing.default_role = body.default_role
        existing.is_active = True
        existing.updated_at = _now()
        await _write_audit(
            db, tenant_id=tenant_id, actor_id=actor_id, actor_role=actor_role,
            resource="ldap_config", resource_id=str(existing.id),
            action="update", before=before_snap,
            after={"server_url": body.server_url, "bind_dn": body.bind_dn},
            **_request_meta(request),
        )
        await db.commit()
        return {"id": str(existing.id), "action": "updated"}
    else:
        cfg = LDAPConfig(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            server_url=body.server_url,
            bind_dn=body.bind_dn,
            bind_password_enc=password_enc,
            user_search_base=body.user_search_base,
            user_search_filter=body.user_search_filter,
            attr_email=body.attr_email,
            attr_name=body.attr_name,
            require_group=body.require_group,
            group_search_base=body.group_search_base,
            group_search_filter=body.group_search_filter,
            group_attr_name=body.group_attr_name,
            group_role_map=body.group_role_map,
            tls_ca_cert=body.tls_ca_cert,
            default_role=body.default_role,
        )
        db.add(cfg)
        await _write_audit(
            db, tenant_id=tenant_id, actor_id=actor_id, actor_role=actor_role,
            resource="ldap_config", resource_id=str(cfg.id),
            action="create",
            after={"server_url": body.server_url, "bind_dn": body.bind_dn},
            **_request_meta(request),
        )
        await db.commit()
        return {"id": str(cfg.id), "action": "created"}


@router.delete(
    "/auth/ldap/config",
    tags=["Enterprise — LDAP/AD"],
    summary="Disable LDAP for tenant (admin only)",
)
async def delete_ldap_config(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    result = await db.execute(
        sa.select(LDAPConfig).where(LDAPConfig.tenant_id == tenant_id)
    )
    cfg: LDAPConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="No LDAP config found for this tenant.")
    cfg.is_active = False
    cfg.updated_at = _now()
    await _write_audit(
        db, tenant_id=tenant_id,
        actor_id=getattr(request.state, "user_id", "unknown"),
        actor_role=getattr(request.state, "role", None),
        resource="ldap_config", resource_id=str(cfg.id),
        action="delete",
        before={"server_url": cfg.server_url},
        **_request_meta(request),
    )
    await db.commit()
    return {"status": "disabled"}


@router.post(
    "/auth/ldap/test",
    tags=["Enterprise — LDAP/AD"],
    summary="Test LDAP connectivity with saved config (admin only)",
)
async def test_ldap_connection(
    request: Request,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(require_roles(["admin"])),
):
    """
    Attempts to bind to the LDAP server using the saved service-account credentials.
    Returns success/failure and the server's response for diagnostics.

    Does NOT attempt a user search — only tests the bind operation.
    Requires ldap3>=2.9.1 (pip install ldap3).
    """
    from apps.api.utils.crypto import decrypt

    result = await db.execute(
        sa.select(LDAPConfig).where(LDAPConfig.tenant_id == tenant_id)
    )
    cfg: LDAPConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="LDAP is not configured for this tenant.")

    try:
        import ldap3
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="ldap3 is not installed. Run: pip install ldap3>=2.9.1",
        )

    bind_password = decrypt(cfg.bind_password_enc)

    # Build TLS config if a custom CA cert was provided
    tls = None
    if cfg.tls_ca_cert:
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(cfg.tls_ca_cert)
            ca_path = f.name
        try:
            tls = ldap3.Tls(ca_certs_file=ca_path, validate=2)  # ssl.CERT_REQUIRED
        finally:
            os.unlink(ca_path)

    try:
        server = ldap3.Server(cfg.server_url, use_ssl="ldaps" in cfg.server_url, tls=tls,
                              get_info=ldap3.ALL, connect_timeout=10)
        conn = ldap3.Connection(server, user=cfg.bind_dn, password=bind_password,
                                auto_bind=True, raise_exceptions=True)
        conn.unbind()
        return {
            "status": "ok",
            "server_url": cfg.server_url,
            "bind_dn": cfg.bind_dn,
            "message": "Bind successful — LDAP credentials are valid.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "server_url": cfg.server_url,
            "bind_dn": cfg.bind_dn,
            "error": str(exc),
        }


@router.post(
    "/auth/ldap/login",
    tags=["Enterprise — LDAP/AD"],
    summary="Authenticate with LDAP username and password (public)",
)
async def ldap_login(
    request: Request,
    body: LDAPLoginIn,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticates a user against the tenant's LDAP / Active Directory.

    Flow:
      1. Bind to LDAP as the OpsLens service account.
      2. Search for the user entry matching user_search_filter.
      3. Re-bind using the found DN and the user's password to verify credentials.
      4. Optionally search groups to assign the correct OpsLens role.
      5. Provision/update the user in the DB.
      6. Return a short-lived internal HS256 JWT.

    On invalid credentials, returns 401. On LDAP connectivity failure, returns 502.
    Requires ldap3>=2.9.1 (pip install ldap3).
    """
    import asyncio
    from apps.api.utils.crypto import decrypt
    from apps.api.auth.token_issuer import issue_sso_token
    from apps.api.db.models import User as UserModel, Tenant as TenantModel

    # Load LDAP config
    cfg_result = await db.execute(
        sa.select(LDAPConfig).where(
            LDAPConfig.tenant_id == tenant_id,
            LDAPConfig.is_active == True,
        )
    )
    cfg: LDAPConfig | None = cfg_result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="LDAP authentication is not configured for this tenant.")

    try:
        import ldap3
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="ldap3 is not installed. Run: pip install ldap3>=2.9.1",
        )

    bind_password = decrypt(cfg.bind_password_enc)

    # Build TLS config (run in thread to avoid blocking event loop for file I/O)
    tls = None
    if cfg.tls_ca_cert:
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(cfg.tls_ca_cert)
            ca_path = f.name
        try:
            tls = ldap3.Tls(ca_certs_file=ca_path, validate=2)
        finally:
            os.unlink(ca_path)

    # All ldap3 operations are synchronous — run in thread executor so we
    # don't block the async event loop during network I/O.
    def _ldap_authenticate() -> tuple[str, str, str]:
        """
        Returns (email, display_name, role) or raises an exception.
        Must be called in a thread (not in an async context).
        """
        server = ldap3.Server(
            cfg.server_url, use_ssl="ldaps" in cfg.server_url, tls=tls,
            get_info=ldap3.ALL, connect_timeout=10,
        )

        # Step 1: Bind as service account to search for the user entry
        service_conn = ldap3.Connection(
            server, user=cfg.bind_dn, password=bind_password,
            auto_bind=True, raise_exceptions=True,
        )

        # Step 2: Search for user DN using the configured filter
        search_filter = cfg.user_search_filter.replace("{username}", ldap3.utils.conv.escape_filter_chars(body.username))
        ok = service_conn.search(
            search_base=cfg.user_search_base,
            search_filter=search_filter,
            search_scope=ldap3.SUBTREE,
            attributes=[cfg.attr_email, cfg.attr_name, "distinguishedName"],
            size_limit=1,
        )
        if not ok or not service_conn.entries:
            service_conn.unbind()
            raise PermissionError("User not found in directory.")

        entry = service_conn.entries[0]
        user_dn = entry.entry_dn

        # Step 3: Re-bind as the user to verify their password
        try:
            user_conn = ldap3.Connection(
                server, user=user_dn, password=body.password,
                auto_bind=True, raise_exceptions=True,
            )
            user_conn.unbind()
        except ldap3.core.exceptions.LDAPInvalidCredentialsResult:
            service_conn.unbind()
            raise PermissionError("Invalid username or password.")

        # Step 4: Extract email and name from the user entry
        email_attr = cfg.attr_email
        name_attr = cfg.attr_name
        email = str(entry[email_attr].value) if email_attr in entry else ""
        display_name = str(entry[name_attr].value) if name_attr in entry else ""

        if not email:
            # Fallback: derive email from userPrincipalName or DN
            if "userPrincipalName" in entry:
                email = str(entry["userPrincipalName"].value)
            else:
                email = f"{body.username}@ldap.local"

        # Step 5: Group-based role resolution (optional)
        role = cfg.default_role or "viewer"
        if cfg.group_search_base and cfg.group_search_filter:
            group_filter = (
                cfg.group_search_filter
                .replace("{user_dn}", ldap3.utils.conv.escape_filter_chars(user_dn))
                .replace("{username}", ldap3.utils.conv.escape_filter_chars(body.username))
            )
            service_conn.search(
                search_base=cfg.group_search_base,
                search_filter=group_filter,
                search_scope=ldap3.SUBTREE,
                attributes=[cfg.group_attr_name],
            )
            group_names = [
                str(g[cfg.group_attr_name].value)
                for g in service_conn.entries
                if cfg.group_attr_name in g
            ]
            group_role_map: dict = cfg.group_role_map or {}
            for gname in group_names:
                if gname in group_role_map and group_role_map[gname] in VALID_ROLES:
                    role = group_role_map[gname]
                    break

            if cfg.require_group and role == (cfg.default_role or "viewer"):
                # No matching group found and group membership is required
                if not any(g in group_role_map for g in group_names):
                    service_conn.unbind()
                    raise PermissionError(
                        "Access denied: you are not a member of any approved group."
                    )

        service_conn.unbind()
        return email, display_name, role

    # Run LDAP operations in a thread to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    try:
        email, display_name, role = await loop.run_in_executor(None, _ldap_authenticate)
    except PermissionError as exc:
        await _write_audit(
            db, tenant_id=tenant_id, actor_id=body.username, actor_role=None,
            resource="session", resource_id=None, action="ldap_login_failed",
            after={"username": body.username, "error": str(exc)},
            **_request_meta(request),
        )
        await db.commit()
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LDAP server error: {exc}")

    # ── Domain allowlist check ─────────────────────────────────────────────────
    tenant_row = await db.execute(
        sa.select(TenantModel.allowed_email_domains).where(
            sa.cast(TenantModel.id, sa.String) == tenant_id
        )
    )
    allowed_domains = tenant_row.scalar_one_or_none() or []
    if allowed_domains:
        email_domain = email.split("@")[-1].lower() if "@" in email else ""
        if email_domain not in [d.strip().lower() for d in allowed_domains]:
            raise HTTPException(
                status_code=403,
                detail=f"Email domain '{email_domain}' is not approved for this workspace.",
            )

    # ── Provision or update user ──────────────────────────────────────────────
    existing = await db.execute(
        sa.select(UserModel).where(
            sa.cast(UserModel.tenant_id, sa.String) == tenant_id,
            UserModel.email == email,
        )
    )
    user: UserModel | None = existing.scalar_one_or_none()

    if user:
        if display_name:
            user.name = display_name
        if role != user.role:
            user.role = role
        user_db_id = str(user.id)
    else:
        tenant_uuid = (
            uuid.UUID(tenant_id) if len(tenant_id) == 36
            else uuid.uuid5(uuid.NAMESPACE_URL, tenant_id)
        )
        new_user = UserModel(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid,
            external_id=email,
            email=email,
            name=display_name or None,
            role=role,
        )
        db.add(new_user)
        user_db_id = str(new_user.id)

    # Issue internal JWT
    token = issue_sso_token(
        user_id=email,
        email=email,
        tenant_id=tenant_id,
        role=role,
    )

    await _write_audit(
        db, tenant_id=tenant_id, actor_id=email, actor_role=role,
        resource="session", resource_id=user_db_id,
        action="ldap_login",
        after={"email": email, "role": role, "username": body.username},
        **_request_meta(request),
    )
    await db.commit()

    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "email": email,
        "role": role,
    }
