"""
Enterprise auth migration
- tenants.allowed_email_domains  — JSONB array of approved email domain strings per tenant
- oidc_configs                   — per-tenant OIDC / OpenID Connect configuration
- ldap_configs                   — per-tenant LDAP / Active Directory configuration

Revision ID: 0003_auth_enterprise
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tenants.allowed_email_domains ─────────────────────────────────────────
    # Stores a list of approved email domain strings, e.g. ["acme.com", "acme.io"].
    # An empty list (the default) means the allowlist is disabled — all domains
    # are accepted. Existing tenants get [] so they are unaffected.
    op.add_column(
        "tenants",
        sa.Column(
            "allowed_email_domains",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        schema="opslens",
    )

    # ── oidc_configs ─────────────────────────────────────────────────────────
    # One row per tenant. Supports Azure AD/Entra ID, Okta, Google Workspace,
    # PingIdentity, and any other standards-compliant OIDC provider.
    op.create_table(
        "oidc_configs",
        sa.Column("id",                  sa.String(),    nullable=False),
        sa.Column("tenant_id",           sa.String(),    nullable=False),

        # OIDC discovery URL — OpsLens fetches /.well-known/openid-configuration
        # from this base URL. Examples:
        #   Azure AD:       https://login.microsoftonline.com/{tenant-id}/v2.0
        #   Okta:           https://{domain}.okta.com/oauth2/default
        #   Google:         https://accounts.google.com
        sa.Column("discovery_url",       sa.String(),    nullable=False),

        # OAuth2 client credentials registered in the IdP
        sa.Column("client_id",           sa.String(),    nullable=False),
        # client_secret_enc: Fernet-encrypted client secret — never stored plaintext
        sa.Column("client_secret_enc",   sa.Text(),      nullable=False),

        # Must exactly match the redirect URI registered in the IdP
        # e.g. "https://app.opslens.ai/api/v1/auth/oidc/callback"
        sa.Column("redirect_uri",        sa.String(),    nullable=False),

        # Space-separated OIDC scopes (e.g. "openid email profile groups")
        sa.Column("scopes",              sa.String(),    nullable=False,
                  server_default="openid email profile"),

        # Maps OIDC claim names to OpsLens fields. Defaults cover most IdPs:
        #   {"email": "email", "name": "name", "role_claim": "groups"}
        # For Azure AD groups: {"role_claim": "roles"} or {"role_claim": "groups"}
        sa.Column("attribute_mapping",   postgresql.JSONB(), nullable=False,
                  server_default='{}'),

        # Role assigned to users logging in for the first time via OIDC
        sa.Column("default_role",        sa.String(),    nullable=False,
                  server_default="viewer"),

        sa.Column("is_active",           sa.Boolean(),   nullable=False,
                  server_default="true"),

        sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at",          sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_oidc_configs_tenant_id"),
        schema="opslens",
    )
    op.create_index("ix_oidc_configs_tenant_id", "oidc_configs", ["tenant_id"], schema="opslens")

    # ── ldap_configs ──────────────────────────────────────────────────────────
    # Optional on-prem integration. One row per tenant. Supports Active Directory,
    # OpenLDAP, and any RFC 4511-compliant LDAP server.
    op.create_table(
        "ldap_configs",
        sa.Column("id",                  sa.String(),    nullable=False),
        sa.Column("tenant_id",           sa.String(),    nullable=False),

        # LDAP server URI — ldap:// or ldaps:// (TLS)
        # Examples:
        #   "ldaps://dc01.corp.example.com:636"
        #   "ldap://openldap.internal:389"
        sa.Column("server_url",          sa.String(),    nullable=False),

        # Service account used by OpsLens to search the directory
        # e.g. "CN=opslens-svc,OU=ServiceAccounts,DC=corp,DC=example,DC=com"
        sa.Column("bind_dn",             sa.String(),    nullable=False),

        # Fernet-encrypted bind password — never stored plaintext
        sa.Column("bind_password_enc",   sa.Text(),      nullable=False),

        # Base DN for user search — the subtree to look in
        # e.g. "OU=Users,DC=corp,DC=example,DC=com"
        sa.Column("user_search_base",    sa.String(),    nullable=False),

        # LDAP filter to find a user by login name. Use {username} as placeholder.
        # Active Directory:  "(&(objectClass=person)(sAMAccountName={username}))"
        # OpenLDAP:          "(&(objectClass=inetOrgPerson)(uid={username}))"
        sa.Column("user_search_filter",  sa.String(),    nullable=False,
                  server_default="(sAMAccountName={username})"),

        # LDAP attribute that contains the user's email address
        sa.Column("attr_email",          sa.String(),    nullable=False,
                  server_default="mail"),

        # LDAP attribute for display name
        sa.Column("attr_name",           sa.String(),    nullable=False,
                  server_default="displayName"),

        # ── Optional group-based role mapping ─────────────────────────────────
        # If require_group is set, users must be a member of at least one group
        # listed in group_role_map or they are rejected at login.
        sa.Column("require_group",       sa.Boolean(),   nullable=False,
                  server_default="false"),

        # Base DN under which to search for group membership
        sa.Column("group_search_base",   sa.String(),    nullable=True),

        # LDAP filter to check group membership. Placeholders:
        #   {user_dn}  — full DN of the authenticated user
        #   {username} — bare username string
        # Active Directory: "(&(objectClass=group)(member={user_dn}))"
        sa.Column("group_search_filter", sa.String(),    nullable=True),

        # LDAP attribute on the group entry that holds the group name
        # Active Directory: "cn"  |  OpenLDAP: "cn"
        sa.Column("group_attr_name",     sa.String(),    nullable=False,
                  server_default="cn"),

        # Maps LDAP group names to OpsLens roles.
        # e.g. {"Engineering Managers": "admin", "SRE": "member", "Contractors": "viewer"}
        sa.Column("group_role_map",      postgresql.JSONB(), nullable=False,
                  server_default='{}'),

        # PEM-encoded CA certificate for ldaps:// connections (optional).
        # Leave null to use the system CA bundle.
        sa.Column("tls_ca_cert",         sa.Text(),      nullable=True),

        # Role for users who have no matching group (only used when require_group=false)
        sa.Column("default_role",        sa.String(),    nullable=False,
                  server_default="viewer"),

        sa.Column("is_active",           sa.Boolean(),   nullable=False,
                  server_default="true"),

        sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at",          sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),

        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_ldap_configs_tenant_id"),
        schema="opslens",
    )
    op.create_index("ix_ldap_configs_tenant_id", "ldap_configs", ["tenant_id"], schema="opslens")


def downgrade() -> None:
    op.drop_index("ix_ldap_configs_tenant_id", table_name="ldap_configs", schema="opslens")
    op.drop_table("ldap_configs", schema="opslens")

    op.drop_index("ix_oidc_configs_tenant_id", table_name="oidc_configs", schema="opslens")
    op.drop_table("oidc_configs", schema="opslens")

    op.drop_column("tenants", "allowed_email_domains", schema="opslens")
