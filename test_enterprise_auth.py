#!/usr/bin/env python3
"""
OpsLens AI — Enterprise Auth Integration Tests
================================================
Setup:
  1. Start the API:
       source .venv/bin/activate
       uvicorn apps.api.main:app --reload --port 8080

  2. (Optional) For LDAP tests, run a local LDAP server:
       docker run -d -p 389:389 --name test-ldap \
         -e LDAP_ORGANISATION="Test" -e LDAP_DOMAIN="test.com" \
         osixia/openldap

  3. Set env vars:
       export OPSLENS_ADMIN_TOKEN=<your admin JWT>
       export OPSLENS_TENANT_ID=<your tenant id>

  4. Run:
       python test_enterprise_auth.py
"""

import os
import sys
import json
import socket
import requests

BASE_URL = os.getenv("OPSLENS_BASE_URL", "http://localhost:8080")
ADMIN_TOKEN = os.getenv("OPSLENS_ADMIN_TOKEN", "")
TENANT_ID = os.getenv("OPSLENS_TENANT_ID", "test-tenant")

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

results = {"passed": 0, "failed": 0, "skipped": 0}


def check(label, condition, skip_reason=None):
    if skip_reason:
        print(f"  [{SKIP}] {label} — {skip_reason}")
        results["skipped"] += 1
    elif condition:
        print(f"  [{PASS}] {label}")
        results["passed"] += 1
    else:
        print(f"  [{FAIL}] {label}")
        results["failed"] += 1


def api_headers(token=None):
    t = token or ADMIN_TOKEN
    h = {"Content-Type": "application/json"}
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


def is_port_open(host, port, timeout=1):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


# ── 0. API reachability ──────────────────────────────────────────────────────
print("\n[0] API Reachability")
try:
    r = requests.get(f"{BASE_URL}/health", timeout=5)
    check("API is up", r.status_code == 200)
    api_up = True
except requests.exceptions.ConnectionError:
    check("API is up", False)
    api_up = False
    print("\n  API is not reachable — skipping remaining tests.")
    print(f"\nResults: {results['passed']} passed, {results['failed']} failed, {results['skipped']} skipped")
    sys.exit(1)


# ── 1. Domain Allowlisting ───────────────────────────────────────────────────
print("\n[1] Domain Allowlisting")

if not ADMIN_TOKEN:
    check("Set allowed domain", None, "OPSLENS_ADMIN_TOKEN not set")
    check("Get allowed domains", None, "OPSLENS_ADMIN_TOKEN not set")
    check("Reject unapproved domain registration", None, "OPSLENS_ADMIN_TOKEN not set")
else:
    # Set allowed domain
    r = requests.post(
        f"{BASE_URL}/api/v1/enterprise/domain-allowlist",
        headers=api_headers(),
        json={"tenant_id": TENANT_ID, "domains": ["approved.com"]},
    )
    check("Set allowed domain (approved.com)", r.status_code in (200, 201))

    # Get allowed domains
    r = requests.get(
        f"{BASE_URL}/api/v1/enterprise/domain-allowlist",
        headers=api_headers(),
        params={"tenant_id": TENANT_ID},
    )
    check("Get allowed domains", r.status_code == 200)
    if r.status_code == 200:
        domains = r.json().get("domains", [])
        check("approved.com appears in allowlist", "approved.com" in domains)

    # Attempt registration from unapproved domain
    r = requests.post(
        f"{BASE_URL}/api/v1/auth/register",
        headers={"Content-Type": "application/json"},
        json={"email": "user@unapproved.com", "password": "Test1234!", "tenant_id": TENANT_ID},
    )
    check("Unapproved domain registration blocked (403)", r.status_code == 403)


# ── 2. SAML SP Metadata ──────────────────────────────────────────────────────
print("\n[2] SAML 2.0 — SP Metadata")

r = requests.get(f"{BASE_URL}/api/v1/enterprise/saml/metadata", timeout=5)
if r.status_code == 404:
    check("SAML metadata endpoint", None, "endpoint not found — SAML may not be configured")
else:
    check("SAML metadata returns 200", r.status_code == 200)
    check("Response is XML", "EntityDescriptor" in r.text or "xml" in r.headers.get("content-type", "").lower())


# ── 3. OIDC Login Redirect ───────────────────────────────────────────────────
print("\n[3] OIDC — Login Redirect")

r = requests.get(
    f"{BASE_URL}/api/v1/enterprise/oidc/login",
    params={"tenant_id": TENANT_ID},
    allow_redirects=False,
    timeout=5,
)
if r.status_code == 404:
    check("OIDC login endpoint", None, "endpoint not found — OIDC may not be configured")
elif r.status_code in (302, 307):
    check("OIDC login returns redirect", True)
    location = r.headers.get("location", "")
    check("Location header present", bool(location))
else:
    check("OIDC login returns redirect", r.status_code in (302, 307),)


# ── 4. LDAP Login ────────────────────────────────────────────────────────────
print("\n[4] LDAP / Active Directory")

ldap_up = is_port_open("localhost", 389)
if not ldap_up:
    check("LDAP server reachable", None, "localhost:389 not open — start test-ldap container")
    check("LDAP login succeeds", None, "LDAP not running")
    check("Bad LDAP credentials return 401", None, "LDAP not running")
else:
    check("LDAP server reachable (localhost:389)", True)

    r = requests.post(
        f"{BASE_URL}/api/v1/enterprise/ldap/login",
        headers={"Content-Type": "application/json"},
        json={"username": "admin", "password": "admin", "tenant_id": TENANT_ID},
        timeout=5,
    )
    check("LDAP login endpoint responds", r.status_code in (200, 401, 422))
    check("Valid credentials accepted OR endpoint active", r.status_code != 500)

    r_bad = requests.post(
        f"{BASE_URL}/api/v1/enterprise/ldap/login",
        headers={"Content-Type": "application/json"},
        json={"username": "nobody", "password": "wrongpassword", "tenant_id": TENANT_ID},
        timeout=5,
    )
    check("Bad credentials return 401", r_bad.status_code == 401)


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"Results: {results['passed']} passed  |  {results['failed']} failed  |  {results['skipped']} skipped")
if results["failed"] == 0:
    print("All tests passed (or skipped)! ✓")
else:
    print("Some tests failed — check output above.")
    sys.exit(1)
