"""
OpsLens AI — SAML SSO + SCIM Provisioning Models
==================================================

SAMLConfig
    Per-tenant IdP configuration. Stored in DB so admins can configure SSO
    without touching environment variables or restarting the API.

    Fields mirror the standard SAML 2.0 SP/IdP contract:
        entity_id              — SP Entity ID (OpsLens side, e.g. "https://app.opslens.ai")
        idp_metadata_url       — URL to IdP SAML metadata XML (auto-refreshed)
        idp_entity_id          — IdP Entity ID (from metadata or manual config)
        idp_sso_url            — IdP Single Sign-On URL
        idp_certificate        — IdP signing certificate (PEM, no headers)
        sp_acs_url             — Assertion Consumer Service URL on OpsLens
        name_id_format         — SAML NameID format (email, persistent, transient)
        attribute_mapping      — JSONB: maps IdP attribute names to OpsLens fields
                                  e.g. {"email": "http://schemas...emailaddress",
                                        "role":  "http://schemas...role"}
        default_role           — Role assigned to new users provisioned via SSO
        sign_requests          — Whether to sign AuthnRequest
        force_authn            — Force re-authentication even if existing session

SCIMConfig
    Per-tenant SCIM 2.0 provisioning configuration.
    Identity providers (Okta, Azure AD, Google Workspace) push user/group
    changes to the SCIM endpoint, keeping OpsLens user list in sync.

    bearer_token_hash  — SHA-256 of the SCIM bearer token (set in IdP)
    sync_groups        — Whether to sync group memberships as role assignments
    group_role_map     — JSONB: maps IdP group names to OpsLens roles
                          e.g. {"Engineering Managers": "manager", "Ops": "engineer"}
    deprovision_action — What to do when IdP deprovisions a user:
                          "deactivate" (default) | "delete"
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from apps.api.db.base import Base


class SAMLConfig(Base):
    __tablename__ = "saml_configs"
    __table_args__ = {"schema": "opslens"}

    id                  = Column(String, primary_key=True)
    tenant_id           = Column(String, nullable=False, unique=True, index=True)

    # Service Provider (OpsLens) settings
    entity_id           = Column(String, nullable=False)
    sp_acs_url          = Column(String, nullable=False)          # callback URL
    sp_slo_url          = Column(String, nullable=True)           # Single Logout URL

    # Identity Provider settings
    idp_metadata_url    = Column(String, nullable=True)           # auto-refresh IdP metadata
    idp_entity_id       = Column(String, nullable=True)
    idp_sso_url         = Column(String, nullable=True)
    idp_slo_url         = Column(String, nullable=True)
    idp_certificate     = Column(Text, nullable=True)             # PEM (no headers)

    # Behaviour
    name_id_format      = Column(String, nullable=False,
                                 default="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress")
    sign_requests       = Column(Boolean, nullable=False, default=True)
    force_authn         = Column(Boolean, nullable=False, default=False)
    is_active           = Column(Boolean, nullable=False, default=True)

    # Attribute mapping: IdP attribute name → OpsLens field
    # e.g. {"email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    #        "first_name": "givenname", "last_name": "surname", "role": "groups"}
    attribute_mapping   = Column(JSONB, nullable=False, default=dict)

    # Default role for new SSO-provisioned users
    default_role        = Column(String, nullable=False, default="viewer")

    created_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc))
    updated_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc),
                                 onupdate=lambda: datetime.now(tz=timezone.utc))


class SCIMConfig(Base):
    __tablename__ = "scim_configs"
    __table_args__ = {"schema": "opslens"}

    id                  = Column(String, primary_key=True)
    tenant_id           = Column(String, nullable=False, unique=True, index=True)

    # Auth
    bearer_token_hash   = Column(String, nullable=False)          # SHA-256 of bearer token
    token_prefix        = Column(String, nullable=True)           # display hint (first 8 chars)

    # Sync settings
    is_active           = Column(Boolean, nullable=False, default=True)
    sync_groups         = Column(Boolean, nullable=False, default=True)
    deprovision_action  = Column(String, nullable=False, default="deactivate")

    # Group → role mapping: {"Engineering Managers": "manager", "SRE": "engineer"}
    group_role_map      = Column(JSONB, nullable=False, default=dict)

    # Stats
    last_sync_at        = Column(DateTime(timezone=True), nullable=True)
    users_provisioned   = Column(String, nullable=True, default="0")
    groups_synced       = Column(String, nullable=True, default="0")

    created_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc))
    updated_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc),
                                 onupdate=lambda: datetime.now(tz=timezone.utc))
