# OpsLens AI — Product Documentation

> **Version:** 1.0 · **Last updated:** 2025

---

## Table of Contents

1. [What is OpsLens AI?](#1-what-is-opslens-ai)
2. [Architecture Overview](#2-architecture-overview)
3. [Getting Started](#3-getting-started)
4. [Log Ingestion](#4-log-ingestion)
5. [Incident Detection](#5-incident-detection)
6. [AI Enrichment & Diagnosis](#6-ai-enrichment--diagnosis)
7. [Routing Rules](#7-routing-rules)
8. [Slack Alerting](#8-slack-alerting)
9. [RRT Briefs](#9-rrt-briefs)
10. [Integrations](#10-integrations)
11. [Simulate Alert](#11-simulate-alert)
12. [Enterprise Features](#12-enterprise-features)
13. [API Reference](#13-api-reference)
14. [Configuration Reference](#14-configuration-reference)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. What is OpsLens AI?

OpsLens AI is an AI-powered incident response platform for engineering teams. It ingests your application logs, detects errors and anomalies in real time, enriches them with context from Jira, GitHub, and Slack, and routes actionable alerts to the right team — all automatically.

### Core Value Loop

```
Logs → Error Detection → RAG Enrichment → AI Diagnosis → Smart Routing → RRT Brief
```

**Before OpsLens AI:**
- Engineers manually search log dashboards during incidents
- P1s take 20+ minutes just to determine the right team to notify
- Slack channels fill with noisy alerts nobody acts on
- Root cause requires piecing together logs, tickets, and PRs manually

**After OpsLens AI:**
- Errors surface automatically within 60 seconds
- The right team is notified via Slack with full context
- AI-generated root cause analysis is ready before engineers even join the incident channel
- RRT briefs provide a structured starting point every time

### Who Uses OpsLens AI

OpsLens is built for engineering teams at startups and growth-stage companies (5–50 engineers) who:
- Ship frequently and can't afford long downtimes
- Have more services than dedicated on-call staff
- Struggle with alert routing across multiple teams
- Want AI-native tooling rather than bolt-on AI features

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      CLIENT APPS                        │
│           Next.js Web UI  ·  REST API  ·  SDK           │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                    API LAYER (FastAPI)                   │
│  /log-ops  /routing-rules  /incidents  /auth  /billing  │
└────┬──────────────────────┬──────────────────────────────┘
     │                      │
┌────▼──────────┐   ┌───────▼───────────────────────────┐
│  PostgreSQL   │   │       Celery Task Workers          │
│  (metadata,  │   │  log_fast_alert  ·  log_scanner    │
│   rules,     │   │  enrich_logs    ·  rrt_generator   │
│   incidents) │   └───────────┬──────────────┬─────────┘
└───────────────┘              │              │
                    ┌──────────▼───┐  ┌───────▼──────────┐
                    │  Qdrant      │  │   LLM Providers  │
                    │ (vector DB   │  │  OpenAI / Claude  │
                    │  for RAG)    │  │  / Ollama         │
                    └──────────────┘  └──────────────────┘
```

### Key Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| API Server | FastAPI + Uvicorn | REST API, auth, routing rules |
| Task Workers | Celery + Redis | Async log processing, alerting |
| Primary DB | PostgreSQL (asyncpg) | Incidents, rules, tenants, billing |
| Vector DB | Qdrant | Semantic search for RAG enrichment |
| AI/LLM | OpenAI, Claude, Ollama | Diagnosis, enrichment, RRT generation |
| Frontend | Next.js + TypeScript | Dashboard, incident feed, configuration |
| Auth | Clerk (JWT) | User auth, organization management |
| Queue | Redis + Celery Beat | Scheduled tasks, fan-out workers |

---

## 3. Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 15+
- Redis 7+
- Qdrant (self-hosted or cloud)

### Environment Variables

Copy `.env.example` to `.env` and fill in the required values:

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/opslens
DATABASE_URL_SYNC=postgresql+psycopg2://user:pass@localhost/opslens

# Redis / Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Auth (Clerk)
CLERK_PUBLISHABLE_KEY=pk_live_...
CLERK_SECRET_KEY=sk_live_...

# LLM Providers (at least one required)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Vector DB
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=           # optional for local

# Slack
LOG_FAST_ALERT_SLACK_WEBHOOK=https://hooks.slack.com/services/...
LOG_SCAN_SLACK_WEBHOOK=https://hooks.slack.com/services/...

# Integrations
JIRA_BASE_URL=https://yourorg.atlassian.net
JIRA_EMAIL=ops@yourcompany.com
JIRA_API_TOKEN=...
GITHUB_TOKEN=ghp_...
```

### Installation

```bash
# API + Workers
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start API server
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload

# Start Celery worker (separate terminal)
celery -A apps.worker.celery_app worker --loglevel=info

# Start Celery Beat scheduler (separate terminal)
celery -A apps.worker.celery_app beat --loglevel=info

# Frontend
cd apps/web
npm install
npm run dev
```

### Quick Verification

```bash
# Health check
curl http://localhost:8000/health

# Submit a test log
curl -X POST http://localhost:8000/api/log-ops/ingest \
  -H "Content-Type: application/json" \
  -d '{"service": "payment-service", "level": "ERROR", "message": "ConnectionPoolExhaustedException"}'
```

---

## 4. Log Ingestion

OpsLens accepts logs via three methods:

### 4.1 REST API (Push)

**Endpoint:** `POST /api/log-ops/ingest`

```json
{
  "service": "payment-service",
  "level": "ERROR",
  "message": "ConnectionPoolExhaustedException: pool size 10 exhausted",
  "timestamp": "2025-01-15T03:22:11Z",
  "metadata": {
    "env": "production",
    "version": "2.4.1",
    "trace_id": "abc-123"
  }
}
```

**Batch ingest:** `POST /api/log-ops/ingest/batch` accepts an array of log objects.

### 4.2 Streaming Webhook

Configure your log aggregator (Datadog, CloudWatch Logs, etc.) to forward structured JSON to your OpsLens webhook endpoint:

```
https://your-opslens-instance.com/api/log-ops/webhook/{tenant_id}
```

### 4.3 SDK (Coming Soon)

Native SDKs for Python, Node.js, and Go will support automatic log forwarding with minimal configuration.

### Log Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `service` | string | ✓ | Service name (used for routing rule matching) |
| `level` | string | ✓ | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `message` | string | ✓ | Log message body |
| `timestamp` | ISO 8601 | — | Defaults to now if omitted |
| `metadata` | object | — | Arbitrary key-value pairs |
| `trace_id` | string | — | Distributed trace ID for correlation |

---

## 5. Incident Detection

### Fast Scan (Real-Time)

The `log_fast_alert` worker runs every minute (configurable). It:

1. Queries recent logs above `ERROR` severity
2. Groups related errors into incident clusters by signature
3. For each cluster, checks if a routing rule matches
4. Dispatches a Slack alert to the matched channel (or fallback webhook)

**Configuration:**
```env
LOG_FAST_ALERT_CRON_MINUTES=1    # Scan frequency in minutes
LOG_FAST_ALERT_WINDOW_MINUTES=5  # Lookback window per scan
```

### Hourly Digest (log_scanner)

The `log_scanner` worker runs every hour and sends a digest of all errors in the past hour to `LOG_SCAN_SLACK_WEBHOOK`. This is separate from the fast scan and does not use routing rules.

### Error Signature Generation

Errors are deduplicated using a normalized signature:
- Stack trace normalization (removes line numbers, memory addresses)
- Exception class extraction
- Message template extraction (replaces dynamic values like UUIDs with `{id}`)

This prevents alert storms — 1,000 occurrences of the same error generates one alert, not 1,000.

---

## 6. AI Enrichment & Diagnosis

When an incident is detected, OpsLens runs the enrichment pipeline:

### 6.1 RAG Enrichment

The `enrich_logs` task:
1. Converts the error signature and message into a vector embedding
2. Queries Qdrant for semantically similar past incidents, runbooks, and post-mortems
3. Fetches recent Jira tickets with matching keywords from the error
4. Fetches recent GitHub PRs merged in the last 24 hours for the affected service
5. Pulls relevant Slack threads from the team's channel

### 6.2 AI Diagnosis

With enriched context, OpsLens calls the configured LLM (OpenAI GPT-4o / Claude) with a structured prompt:

- **Root cause hypothesis** — what likely caused this error
- **Contributing factors** — recent code changes, config changes, external dependencies
- **Remediation steps** — specific, actionable steps to resolve
- **Severity assessment** — P1/P2/P3 with justification
- **Related artifacts** — links to relevant Jira tickets, PRs, runbook sections

### 6.3 Configuring AI Providers

```env
# Primary LLM (default: openai)
AI_PROVIDER=openai           # openai | anthropic | ollama

# OpenAI
OPENAI_MODEL=gpt-4o
OPENAI_API_KEY=sk-...

# Anthropic Claude (enterprise / BAA)
ANTHROPIC_MODEL=claude-opus-4-5
ANTHROPIC_API_KEY=sk-ant-...

# Ollama (on-prem / private)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3
```

---

## 7. Routing Rules

Routing rules determine which Slack channel receives alerts for which services and error types.

### Rule Schema

| Field | Type | Description |
|-------|------|-------------|
| `team_name` | string | Display name (e.g., "Payment Team") |
| `slack_webhook` | string | Slack incoming webhook URL |
| `service_filter` | string | Service name pattern (e.g., `payment-*`) |
| `error_filter` | string | Error message pattern (e.g., `ConnectionPool`) |
| `priority` | integer | Lower = higher priority. Rules are evaluated in order. |
| `is_active` | boolean | Enable/disable without deleting |

### Rule Evaluation

For each incident:
1. All active rules for the tenant are loaded, sorted by `priority` ascending
2. The first rule where both `service_filter` and `error_filter` match is selected
3. If no rule matches, the fallback webhook (`LOG_FAST_ALERT_SLACK_WEBHOOK`) is used

**Pattern matching:** Both `service_filter` and `error_filter` support substring matching (case-insensitive). If left empty, the field matches all values.

### Creating Rules via UI

1. Navigate to **Routing Rules** in the sidebar
2. Click **+ New Rule**
3. Fill in team name, Slack webhook, and optional filters
4. Set priority (1 = highest)
5. Click **Save Rule**

### API

```bash
# List rules
GET /api/routing-rules

# Create rule
POST /api/routing-rules
{
  "team_name": "Payment Team",
  "slack_webhook": "https://hooks.slack.com/services/...",
  "service_filter": "payment",
  "error_filter": "",
  "priority": 10
}

# Update rule
PATCH /api/routing-rules/{rule_id}

# Delete rule
DELETE /api/routing-rules/{rule_id}
```

### Important: Tenant Context

Routing rules are scoped to your Clerk organization. If your Clerk JWT changes context (e.g., switching between personal and org context), rules saved in one context will not be visible in another. Always ensure you are signed in under the correct organization when managing routing rules.

---

## 8. Slack Alerting

### Alert Format

OpsLens sends rich Slack messages with:

- **Service name** and error signature
- **Severity** (P1/P2/P3 badge)
- **Occurrence count** in the detection window
- **AI diagnosis summary** (first 200 chars)
- **Links** to related Jira tickets and GitHub PRs
- **"View RRT Brief"** button (links to dashboard)

### Webhook Setup (Slack)

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Enable **Incoming Webhooks**
3. Add a webhook for the target channel
4. Copy the webhook URL into OpsLens routing rule or env var

### Fallback Webhook

If no routing rule matches, `LOG_FAST_ALERT_SLACK_WEBHOOK` is used. This should point to a general ops channel as a safety net.

The hourly digest always goes to `LOG_SCAN_SLACK_WEBHOOK` regardless of routing rules.

---

## 9. RRT Briefs

**Rapid Response Triage (RRT) Briefs** are structured incident documents auto-generated by OpsLens AI.

### Brief Structure

1. **Incident Summary** — One-paragraph overview of what happened
2. **Timeline** — First detected, alert sent, team notified
3. **Affected Services** — Services involved and their current status
4. **Root Cause Hypothesis** — AI-generated root cause with confidence level
5. **Evidence** — Log samples, error rates, contributing factors
6. **Related Artifacts** — Jira ticket links, GitHub PR links, Slack thread links
7. **Recommended Actions** — Step-by-step remediation checklist
8. **Escalation Path** — Who to page if initial steps don't resolve

### Accessing Briefs

- **Dashboard:** Click any incident card → "View RRT Brief" button
- **Slack:** Alerts include a direct link to the brief
- **API:** `GET /api/incidents/{incident_id}/rrt-brief`

---

## 10. Integrations

### Jira

OpsLens searches your Jira project for tickets related to the error service and keywords.

**Required scopes:** `read:jira-work`

**Configuration:**
```env
JIRA_BASE_URL=https://yourorg.atlassian.net
JIRA_EMAIL=ops@yourcompany.com
JIRA_API_TOKEN=...          # Create at: id.atlassian.com/manage-profile/security/api-tokens
JIRA_PROJECT_KEY=ENG        # Optional: limit search to specific project
```

### GitHub

OpsLens fetches recent merged PRs for services that triggered an incident, helping identify if a recent deploy caused the issue.

**Required scopes:** `repo:read`

**Configuration:**
```env
GITHUB_TOKEN=ghp_...
GITHUB_ORG=your-org-name    # Optional: limit search to your org
```

### Slack

Two modes:
- **Incoming Webhooks** — for sending alerts (simpler, no OAuth)
- **Slack App (OAuth)** — for reading thread context in RAG enrichment (coming soon)

---

## 11. Simulate Alert

The Simulate Alert feature lets you test your routing rules without waiting for a real incident.

### Using the UI

1. Navigate to **Incidents** in the sidebar
2. Click **Simulate Alert**
3. Enter a service name and error message
4. Click **Run Simulation**
5. The result shows:
   - Which routing rule matched (if any)
   - Which Slack channel the alert would be sent to
   - Whether it used the fallback webhook

### API

```bash
POST /api/log-ops/simulate
{
  "service_name": "payment-service",
  "error_message": "ConnectionPoolExhaustedException: pool exhausted"
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Alert dispatched",
  "routed_to": ["PaymentTeam"],
  "routing_used_fallback": false,
  "error_signature": "ConnectionPoolExhaustedException",
  "service_name": "payment-service"
}
```

If `routing_used_fallback: true` appears, check that your routing rules are saved under the correct Clerk organization context.

---

## 12. Enterprise Features

### Authentication

OpsLens supports three auth methods:

| Method | Use Case |
|--------|----------|
| Clerk (default) | SaaS / cloud deployments |
| SAML 2.0 (SSO) | Enterprise identity providers (Okta, Azure AD) |
| LDAP / Active Directory | On-premise enterprise environments |

**SAML Configuration:**
```env
SAML_SP_ENTITY_ID=https://your-opslens.com/saml/metadata
SAML_IDP_METADATA_URL=https://your-idp.com/metadata
```

**LDAP Configuration:**
```env
LDAP_SERVER=ldap://ad.yourcompany.com
LDAP_BASE_DN=DC=yourcompany,DC=com
LDAP_BIND_DN=CN=opslens-svc,OU=Service Accounts,DC=yourcompany,DC=com
LDAP_BIND_PASSWORD=...
```

### RBAC (Role-Based Access Control)

| Role | Permissions |
|------|-------------|
| Admin | Full access: manage rules, integrations, billing, users |
| Engineer | View incidents, run simulations, view RRT briefs |
| Viewer | Read-only access to incidents and briefs |

### SOC 2 Audit Trail

All user actions are logged to the `audit_log` table with timestamp, user ID, action type, and affected resource. Audit logs are immutable and retained for 2 years.

### Data Encryption

- Credentials (Slack webhooks, API tokens) are encrypted at rest using AES-256 (Fernet)
- All API traffic requires HTTPS/TLS
- Database connections use TLS

---

## 13. API Reference

All API endpoints require a valid JWT in the `Authorization: Bearer <token>` header.

### Log Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/log-ops/ingest` | Ingest a single log entry |
| POST | `/api/log-ops/ingest/batch` | Ingest multiple log entries |
| POST | `/api/log-ops/simulate` | Simulate an alert (test routing) |
| GET  | `/api/log-ops/incidents` | List recent incidents |
| GET  | `/api/log-ops/incidents/{id}` | Get incident details + RRT brief |

### Routing Rules

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET    | `/api/routing-rules` | List all routing rules |
| POST   | `/api/routing-rules` | Create a routing rule |
| PATCH  | `/api/routing-rules/{id}` | Update a routing rule |
| DELETE | `/api/routing-rules/{id}` | Delete a routing rule |

### Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET    | `/api/admin/tenants` | List tenants (admin only) |
| GET    | `/api/admin/usage` | Usage metrics for billing |
| GET    | `/health` | Health check (no auth required) |

---

## 14. Configuration Reference

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_FAST_ALERT_CRON_MINUTES` | `1` | How often fast scan runs |
| `LOG_FAST_ALERT_WINDOW_MINUTES` | `5` | Lookback window for fast scan |
| `LOG_SCAN_CRON_HOURS` | `1` | Hourly digest frequency |
| `AI_PROVIDER` | `openai` | `openai`, `anthropic`, `ollama` |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model to use |
| `MAX_INCIDENT_BATCH` | `100` | Max incidents per scan cycle |
| `QDRANT_COLLECTION` | `opslens_logs` | Qdrant collection name |

### Celery Beat Schedule

Tasks are scheduled in `apps/worker/celery_app.py`:

```python
beat_schedule = {
    "log-fast-alert": {
        "task": "logs.fast_scan_all_tenants",
        "schedule": crontab(minute=f"*/{settings.LOG_FAST_ALERT_CRON_MINUTES}"),
    },
    "log-hourly-scan": {
        "task": "logs.run_log_scanner",
        "schedule": crontab(minute=0),
    },
}
```

---

## 15. Troubleshooting

### Alerts going to wrong Slack channel

**Symptoms:** Alerts arrive in the fallback channel instead of the expected team channel.

**Diagnosis:**
1. Run a Simulate Alert from the UI
2. Check the `routed_to` field in the response
3. If `routing_used_fallback: true`, your routing rules are not matching

**Common causes:**
- Routing rule was saved under a different Clerk organization context
- Service name in the rule doesn't match the service name in the log (case-sensitive)
- Rule's `is_active` flag is false
- Rule's `priority` is lower than another matching rule

**Fix:** Delete and recreate the routing rule while signed in under the correct Clerk organization.

### "Token expired" errors when saving rules

**Cause:** The session JWT expires after ~1 hour. Refresh the browser page to get a new token, then retry.

### Alembic migration fails

```bash
# Check current migration state
alembic current

# Run pending migrations
alembic upgrade head

# If stuck, check for stale migration locks in PostgreSQL
SELECT * FROM alembic_version;
```

### Worker not processing logs

1. Verify Redis is running: `redis-cli ping` → `PONG`
2. Check Celery worker is connected: look for `ready` in worker logs
3. Check Beat scheduler is running for scheduled tasks
4. Verify `DATABASE_URL` is accessible from the worker host

### Qdrant connection errors

```bash
# Test Qdrant connectivity
curl http://localhost:6333/healthz

# Check collections exist
curl http://localhost:6333/collections
```

If the collection doesn't exist, it's created automatically on first use. If it exists but is empty, run a re-index of historical logs via the admin API.

---

*For support, email [hello@opslens.ai](mailto:hello@opslens.ai) or file an issue on GitHub.*
