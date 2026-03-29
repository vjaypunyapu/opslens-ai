"""
OpsLens AI — python3-saml Integration Helpers
===============================================
Wraps the python3-saml library (PyPI: python3-saml, imports: onelogin.saml2)
to handle the SAML 2.0 Service Provider (SP) flow for OpsLens.

Installation requirements:
    pip install python3-saml>=2.6.0
    # Native libraries also required:
    #   Debian/Ubuntu:  apt-get install libxml2-dev libxmlsec1-dev libxmlsec1-openssl
    #   Alpine Linux:   apk add libxmlsec1-dev
    #   macOS:          brew install libxmlsec1

Supported IdPs:
    Azure AD / Entra ID, Okta, Google Workspace, OneLogin, PingIdentity,
    ADFS, and any other SAML 2.0-compliant identity provider.

python3-saml docs: https://github.com/SAML-Toolkits/python3-saml

Functions
---------
build_saml_settings(cfg)
    Converts a SAMLConfig ORM row into the settings dict expected by
    OneLogin_Saml2_Auth. Handles missing optional fields gracefully.

prepare_saml_request(request)
    Converts a FastAPI Request into the dict that OneLogin_Saml2_Auth requires.
    Must be called with form data already consumed (pass form_data separately).

get_login_url(cfg, relay_state)
    Generates the IdP redirect URL that initiates an SP-initiated SSO flow.
    Returns the full URL — redirect the user's browser here.

parse_saml_response(cfg, request, form_data) -> (email, name, attributes)
    Validates the SAMLResponse POST from the IdP. Returns the NameID (email),
    display name, and the full attribute dict from the assertion. Raises
    ValueError on validation failure (expired, signature mismatch, etc.).

extract_role(cfg, attributes) -> str
    Uses SAMLConfig.attribute_mapping to extract the role claim from assertion
    attributes and look it up in the role map. Falls back to cfg.default_role.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..utils.logging import get_logger

if TYPE_CHECKING:
    from fastapi import Request
    from ..models.saml import SAMLConfig

logger = get_logger(__name__)


def build_saml_settings(cfg: "SAMLConfig") -> dict:
    """
    Build the python3-saml settings dict from a SAMLConfig DB row.

    python3-saml expects a specific nested structure. This function maps the
    flat DB columns to the required shape, inserting safe defaults for optional
    fields (SP certificate, IdP SLO URL) that are common in minimal deployments.
    """
    settings: dict = {
        "strict": True,   # Strict mode enforces XML validity, expiry, and signatures.
        "debug": False,   # Set to True temporarily for IdP integration debugging.

        "sp": {
            # The SP Entity ID must match exactly what was registered in the IdP.
            "entityId": cfg.entity_id,

            "assertionConsumerService": {
                "url": cfg.sp_acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },

            # SLO is optional — many deployments only configure SSO (not logout).
            "singleLogoutService": {
                "url": cfg.sp_slo_url or "",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },

            "NameIDFormat": cfg.name_id_format,

            # SP signing credentials — leave empty unless SP-signed AuthnRequests
            # are required by the IdP. Most cloud IdPs do not require this.
            "x509cert": "",
            "privateKey": "",
        },

        "idp": {
            "entityId": cfg.idp_entity_id or "",

            "singleSignOnService": {
                "url": cfg.idp_sso_url or "",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },

            "singleLogoutService": {
                "url": cfg.idp_slo_url or "",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },

            # PEM certificate (no BEGIN/END headers) — used to verify IdP signatures.
            # This is the certificate from the IdP metadata or admin console.
            "x509cert": cfg.idp_certificate or "",
        },

        "security": {
            "authnRequestsSigned": cfg.sign_requests,
            "wantAssertionsSigned": True,
            "wantMessagesSigned": False,
            "wantNameId": True,
            "wantNameIdEncrypted": False,
            "wantAssertionsEncrypted": False,
            "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
        },
    }

    return settings


def prepare_saml_request(request: "Request", form_data: dict | None = None) -> dict:
    """
    Convert a FastAPI Request into the dict that OneLogin_Saml2_Auth requires.

    python3-saml expects a WSGI-style "request" dict with specific keys.
    FastAPI/Starlette uses a different interface, so this adapts between them.

    Args:
        request:   The FastAPI Request object.
        form_data: Pre-consumed form data dict (only needed for ACS POST handler).
                   Pass None for GET-based flows (login redirect, metadata).
    """
    scheme = request.url.scheme
    host = request.headers.get("Host", "") or request.url.netloc
    port = str(request.url.port or (443 if scheme == "https" else 80))

    return {
        "https": "on" if scheme == "https" else "off",
        "http_host": host,
        "server_port": port,
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": form_data or {},
        # lowercase_urlencoding must match what the IdP sends; "off" is safe default.
        "lowercase_urlencoding": False,
    }


def get_login_url(cfg: "SAMLConfig", relay_state: str = "") -> str:
    """
    Generate the IdP redirect URL that initiates an SP-initiated SAML SSO flow.

    The returned URL should be sent as an HTTP 302 redirect to the user's browser.
    The browser follows it to the IdP, authenticates, and POSTs the SAML assertion
    back to the ACS endpoint.

    Args:
        cfg:         SAMLConfig DB row for the tenant.
        relay_state: Optional URL to redirect to after successful login (e.g. the
                     original page the user was trying to reach). The IdP will
                     echo this back in the ACS POST.

    Returns:
        Full IdP SSO URL with the AuthnRequest as a query parameter.

    Raises:
        ImportError:  python3-saml not installed — see module docstring.
        RuntimeError: SAML settings are invalid or IdP URL is missing.
    """
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
    except ImportError as exc:
        raise ImportError(
            "python3-saml is required for SAML SSO. "
            "Install it with: pip install python3-saml>=2.6.0 "
            "(also requires libxmlsec1-dev native library)"
        ) from exc

    saml_settings = build_saml_settings(cfg)

    # python3-saml requires a request dict even for login URL generation.
    # We build a minimal synthetic one since there is no real HTTP request here.
    dummy_request: dict = {
        "https": "on",
        "http_host": cfg.sp_acs_url.split("/")[2] if "//" in cfg.sp_acs_url else "app.opslens.ai",
        "server_port": "443",
        "script_name": "/api/v1/auth/saml/login",
        "get_data": {},
        "post_data": {},
        "lowercase_urlencoding": False,
    }

    auth = OneLogin_Saml2_Auth(dummy_request, saml_settings)
    return auth.login(return_to=relay_state or "", force_authn=cfg.force_authn)


def parse_saml_response(
    cfg: "SAMLConfig",
    request: "Request",
    form_data: dict,
) -> tuple[str, str, dict]:
    """
    Validate the SAMLResponse POST from the IdP and extract user attributes.

    This is the core of the ACS handler. python3-saml verifies:
        • XML signature against the IdP certificate
        • Assertion expiry (NotOnOrAfter)
        • Audience restriction (SP Entity ID)
        • Destination URL (SP ACS URL)
        • In-response-to (prevents replay attacks)

    Args:
        cfg:       SAMLConfig DB row for the tenant.
        request:   The FastAPI Request for the ACS POST (to extract host/scheme).
        form_data: Consumed form data dict containing SAMLResponse and RelayState.

    Returns:
        Tuple of (email, display_name, attributes):
            email:        NameID value (typically the user's email address).
            display_name: Display name from assertion attributes (may be empty).
            attributes:   Full dict of assertion attributes as {name: [values]}.

    Raises:
        ImportError:  python3-saml not installed.
        ValueError:   SAML validation failed. Message contains error details safe
                      to log but not to expose verbatim to end users.
    """
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
    except ImportError as exc:
        raise ImportError(
            "python3-saml is required for SAML SSO. "
            "Install it with: pip install python3-saml>=2.6.0"
        ) from exc

    req = prepare_saml_request(request, form_data)
    saml_settings = build_saml_settings(cfg)

    auth = OneLogin_Saml2_Auth(req, saml_settings)
    auth.process_response()

    errors = auth.get_errors()
    if errors:
        reason = auth.get_last_error_reason() or ", ".join(errors)
        logger.warning("SAML validation errors for tenant %s: %s", cfg.tenant_id, reason)
        raise ValueError(f"SAML assertion validation failed: {reason}")

    if not auth.is_authenticated():
        raise ValueError("SAML authentication was not successful (not authenticated).")

    email: str = auth.get_nameid() or ""
    attributes: dict = auth.get_attributes()

    # Extract display name using attribute_mapping or common fallback attribute names
    mapping: dict = cfg.attribute_mapping or {}
    name_key = mapping.get("name") or mapping.get("displayName")
    display_name = ""
    if name_key and name_key in attributes:
        display_name = (attributes[name_key] or [""])[0]
    else:
        # Try common attribute names for display name
        for fallback in ("displayName", "urn:oid:2.16.840.1.113730.3.1.241",
                         "givenName", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname"):
            if fallback in attributes:
                display_name = (attributes[fallback] or [""])[0]
                break

    return email, display_name, attributes


def extract_role(cfg: "SAMLConfig", attributes: dict) -> str:
    """
    Determine the OpsLens role from SAML assertion attributes.

    Looks up SAMLConfig.attribute_mapping["role"] (or "groups") in the assertion
    attributes to find the role claim values. Then matches them against
    SAMLConfig.attribute_mapping.get("role_map", {}) if present, otherwise
    falls back to cfg.default_role.

    Example configuration:
        attribute_mapping = {
            "role": "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
            "role_map": {"GlobalAdmins": "admin", "Developers": "member"}
        }

    Args:
        cfg:        SAMLConfig row with attribute_mapping configured.
        attributes: Assertion attributes dict from parse_saml_response().

    Returns:
        OpsLens role string: "admin" | "member" | "viewer".
    """
    from ..models.rbac import VALID_ROLES

    mapping: dict = cfg.attribute_mapping or {}
    role_attr_name = mapping.get("role") or mapping.get("groups")
    role_map: dict = mapping.get("role_map", {})

    if role_attr_name and role_attr_name in attributes:
        claim_values = attributes[role_attr_name] or []
        # Iterate values in order — first match wins
        for value in claim_values:
            mapped_role = role_map.get(value)
            if mapped_role and mapped_role in VALID_ROLES:
                return mapped_role

    return cfg.default_role or "viewer"
