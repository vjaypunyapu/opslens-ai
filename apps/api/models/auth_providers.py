"""
OpsLens AI — OIDC and LDAP Auth Provider Models
=================================================

OIDCConfig
    Per-tenant OpenID Connect SSO configuration. Supports any standards-compliant
    OIDC provider: Azure AD/Entra ID, Okta, Google Workspace, PingIdentity, etc.

    Authentication flow:
        1. Admin configures client_id, client_secret, and discovery_url.
        2. Users click "Login with SSO" → GET /api/v1/auth/oidc/login?tenant_id=...
        3. OpsLens fetches the discovery document and redirects to the IdP.
        4. User authenticates at the IdP.
        5. IdP redirects to GET /api/v1/auth/oidc/callback with an auth code.
        6. OpsLens exchanges the code for tokens, extracts user info, provisions
           the user in the DB, issues an internal HS256 JWT, and redirects to
           {FRONTEND_URL}/auth/sso-callback?token=<jwt>.
        7. The frontend stores the JWT and sends it as a Bearer token.

LDAPConfig
    Per-tenant LDAP / Active Directory configuration for on-prem customers.

    Authentication flow:
        1. Admin configures server_url, bind_dn, and search settings.
        2. Users POST credentials to /api/v1/auth/ldap/login.
        3. OpsLens binds to LDAP as the service account, searches for the user,
           then re-binds with the user's own password to verify it.
        4. Optionally checks group membership for role assignment.
        5. Provisions/updates the user in the DB and issues an internal JWT.

    Security note: bind_password_enc stores the service-account password
    encrypted with Fernet (AES-256). It is never returned in API responses.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from apps.api.db.base import Base


class OIDCConfig(Base):
    __tablename__ = "oidc_configs"
    __table_args__ = {"schema": "opslens"}

    id                  = Column(String, primary_key=True)
    tenant_id           = Column(String, nullable=False, unique=True, index=True)

    # OIDC discovery endpoint — the base URL where OpsLens fetches
    # /.well-known/openid-configuration to find token/userinfo endpoints.
    # Azure AD:    https://login.microsoftonline.com/{tenant-id}/v2.0
    # Okta:        https://{domain}.okta.com/oauth2/default
    # Google:      https://accounts.google.com
    discovery_url       = Column(String, nullable=False)

    # OAuth2 application credentials registered in the IdP
    client_id           = Column(String, nullable=False)
    # Fernet-encrypted client secret (never stored or returned in plaintext)
    client_secret_enc   = Column(Text, nullable=False)

    # Must exactly match the redirect URI registered in the IdP.
    # e.g. "https://app.opslens.ai/api/v1/auth/oidc/callback"
    redirect_uri        = Column(String, nullable=False)

    # Space-separated OIDC scopes
    scopes              = Column(String, nullable=False, default="openid email profile")

    # Maps OIDC claim names → OpsLens fields.
    # Recognised keys: "email", "name", "role_claim"
    # role_claim points to the claim whose value(s) are looked up in role_map.
    # Example for Azure AD groups:
    #   {"email": "email", "name": "name", "role_claim": "roles"}
    # Example for Okta groups:
    #   {"email": "email", "name": "name", "role_claim": "groups"}
    attribute_mapping   = Column(JSONB, nullable=False, default=dict)

    # Maps IdP role/group claim values to OpsLens roles.
    # e.g. {"Engineering-Admins": "admin", "SRE": "member", "Contractors": "viewer"}
    # Users with no matching group get default_role.
    role_map            = Column(JSONB, nullable=False, default=dict)

    # Role assigned to new users with no matching group in role_map
    default_role        = Column(String, nullable=False, default="viewer")

    is_active           = Column(Boolean, nullable=False, default=True)

    created_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc))
    updated_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc),
                                 onupdate=lambda: datetime.now(tz=timezone.utc))


class LDAPConfig(Base):
    __tablename__ = "ldap_configs"
    __table_args__ = {"schema": "opslens"}

    id                  = Column(String, primary_key=True)
    tenant_id           = Column(String, nullable=False, unique=True, index=True)

    # LDAP server URI — ldap:// for plaintext, ldaps:// for TLS (recommended).
    # Examples:
    #   "ldaps://dc01.corp.example.com:636"   ← Active Directory over TLS
    #   "ldap://openldap.internal:389"         ← OpenLDAP (use ldaps in prod)
    server_url          = Column(String, nullable=False)

    # Service account DN used by OpsLens to search the directory.
    # Active Directory: "CN=opslens-svc,OU=ServiceAccounts,DC=corp,DC=example,DC=com"
    # OpenLDAP:         "cn=opslens-svc,ou=serviceaccounts,dc=corp,dc=example,dc=com"
    bind_dn             = Column(String, nullable=False)

    # Fernet-encrypted bind password — never stored or returned in plaintext.
    bind_password_enc   = Column(Text, nullable=False)

    # Base DN of the subtree where user entries reside.
    # e.g. "OU=Users,DC=corp,DC=example,DC=com"
    user_search_base    = Column(String, nullable=False)

    # LDAP search filter to locate a user by login name.
    # Use {username} as the placeholder for the submitted username.
    # Active Directory: "(&(objectClass=person)(sAMAccountName={username}))"
    # OpenLDAP:         "(&(objectClass=inetOrgPerson)(uid={username}))"
    user_search_filter  = Column(String, nullable=False,
                                  default="(sAMAccountName={username})")

    # LDAP attribute containing the user's email address
    attr_email          = Column(String, nullable=False, default="mail")

    # LDAP attribute containing the user's display name
    attr_name           = Column(String, nullable=False, default="displayName")

    # ── Group-based role assignment ───────────────────────────────────────────

    # If True, the user must belong to at least one group in group_role_map
    # or the login is rejected (even with a valid password).
    require_group       = Column(Boolean, nullable=False, default=False)

    # Base DN to search for group entries.
    # e.g. "OU=Groups,DC=corp,DC=example,DC=com"
    group_search_base   = Column(String, nullable=True)

    # LDAP filter to find groups the user belongs to.
    # Placeholders: {user_dn}, {username}
    # Active Directory: "(&(objectClass=group)(member={user_dn}))"
    # OpenLDAP (memberOf): "(uniqueMember={user_dn})"
    group_search_filter = Column(String, nullable=True)

    # LDAP attribute on group entries that holds the group name (for role_map lookup)
    group_attr_name     = Column(String, nullable=False, default="cn")

    # Maps LDAP group cn values to OpsLens roles.
    # e.g. {"Engineering Managers": "admin", "SRE": "member", "Contractors": "viewer"}
    group_role_map      = Column(JSONB, nullable=False, default=dict)

    # PEM-encoded CA certificate for ldaps:// validation.
    # Leave null to use the system CA bundle (appropriate for public CAs).
    # Required for self-signed or private CA certificates.
    tls_ca_cert         = Column(Text, nullable=True)

    # Role for authenticated users with no matching group_role_map entry.
    # Only used when require_group is False.
    default_role        = Column(String, nullable=False, default="viewer")

    is_active           = Column(Boolean, nullable=False, default=True)

    created_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc))
    updated_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc),
                                 onupdate=lambda: datetime.now(tz=timezone.utc))
