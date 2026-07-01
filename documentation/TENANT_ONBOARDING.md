# Tenant Onboarding Guide

How to provision a new client workspace ("tenant") in OpsLens AI, from
scratch through the client's first login.

Production app URL: `https://www.opslensai.com`

## Overview

OpsLens AI is multi-tenant. Every tenant is a row in the `tenants` table
(`apps/api/db/models.py`), and all customer data (users, integrations,
documents, insights, incidents, alerts) is scoped to a `tenant_id`.
Vector search is isolated further via a dedicated Qdrant collection per
tenant (`opslens_{tenant_id}`).

There are two ways a tenant comes into existence:

1. **Platform-admin provisioning** (below) — the intended path for a real
   customer. You explicitly create the workspace and invite their first
   admin.
2. **Self-service auto-provisioning** — if a user reaches the API with a
   valid login for a tenant that doesn't exist yet, the backend creates
   it automatically and makes that user the workspace admin
   (`apps/api/auth/dependencies.py`). This is a fallback, not the normal
   flow for onboarding a paying customer.

This guide covers path 1.

## Prerequisites

- Your email must be listed in the `PLATFORM_ADMIN_EMAILS` environment
  variable on the production API. This is a comma-separated allowlist
  (`apps/api/routers/platform.py`) — anyone not on it gets `403`.
- A valid bearer token (JWT) for that account, obtained by logging into
  `https://www.opslensai.com` and copying the session token, or via
  whatever auth flow your Clerk/Auth0 setup provides.

All commands below assume:

```bash
export OPSLENS_API=https://www.opslensai.com/api/v1
export ADMIN_TOKEN="<your platform-admin bearer token>"
```

## Step 1 — Create the tenant

```bash
curl -X POST "$OPSLENS_API/platform/tenants" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ACME Corp",
    "plan": "starter",
    "slug": "acme-corp"
  }'
```

- `plan` — one of `starter`, `growth`, `enterprise` (default `starter`).
- `slug` — optional; auto-derived from `name` if omitted, and
  auto-suffixed if it collides with an existing tenant.

Response:

```json
{
  "tenant_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "name": "ACME Corp",
  "slug": "acme-corp",
  "plan": "starter",
  "created_at": "2026-07-01T18:20:00Z"
}
```

Save `tenant_id` — you need it for every following step.

## Step 2 — Invite the client's first admin

```bash
curl -X POST "$OPSLENS_API/platform/tenants/$TENANT_ID/invite" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@acme.com",
    "role": "admin",
    "expires_in_hours": 72
  }'
```

This creates a `PendingInvite` row, emails the invite link via SendGrid,
and returns it in the response:

```json
{
  "invite_id": "...",
  "email": "admin@acme.com",
  "invite_url": "https://www.opslensai.com/sign-up?token=...",
  "expires_at": "2026-07-04T18:20:00Z",
  "email_sent": true
}
```

If `email_sent` is `false`, share `invite_url` with the client directly
(email delivery may have failed, but the link itself is valid).

## Step 3 — Client accepts the invite

1. The client opens `invite_url` and completes sign-up/OAuth.
2. The frontend calls `POST /api/v1/admin/invites/{invite_id}/redeem`
   with the client's new session token. This sets their role in the
   tenant to whatever was specified in Step 2 and marks the invite
   accepted. No action needed from you — this happens automatically as
   part of the sign-up flow.

After this, the client is logged in with the `admin` role in their new
workspace and can invite teammates from within the app.

## Step 4 — Provision the vector store collection

The API does **not** create the tenant's Qdrant collection automatically.
Run this once per new tenant so document embeddings/RAG search work:

```bash
python scripts/init_qdrant_collections.py --tenant-id $TENANT_ID
```

(Requires `QDRANT_URL` / `QDRANT_API_KEY` pointed at the production
Qdrant instance — run this from an environment with access, e.g. the
API server or a bastion, not from an arbitrary laptop unless it's
network-reachable.)

To backfill collections for every tenant that's missing one:

```bash
python scripts/init_qdrant_collections.py --all-tenants
```

## Step 5 — Client connects integrations

Once logged in, the client uses the in-app onboarding wizard
(`apps/web/src/app/onboarding/page.tsx`) to connect Jira/GitHub/Slack,
configure alert routing, and set up notification rules. No platform-admin
action is required for this step.

## Useful platform-admin endpoints

| Purpose | Endpoint |
|---|---|
| List all tenants + usage stats | `GET /platform/tenants` |
| Get one tenant's detail | `GET /platform/tenants/{tenant_id}` |
| Create a tenant | `POST /platform/tenants` |
| Invite first admin | `POST /platform/tenants/{tenant_id}/invite` |
| List pending invites for a tenant | `GET /platform/tenants/{tenant_id}/invites` |

Source: `apps/api/routers/platform.py`.

## Notes / known gaps

- There's no admin dashboard UI for these steps yet — they're raw API
  calls only.
- `max_integrations` / `max_users` limits exist on the `tenants` table
  but aren't enforced by any endpoint yet, so `plan` is informational for
  now.
- Domain-restricted self-signup (`allowed_email_domains` on the tenant)
  is supported by the backend but has no UI to configure it — set it via
  a direct DB update on `tenants.allowed_email_domains` if a client needs
  SSO-domain-only signup.
