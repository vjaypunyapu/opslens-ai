# OpsLens AI — Project Memory
_Last updated: 2026-03-17_

## What OpsLens AI Is
An operational intelligence copilot for engineering teams. It sits on top of your existing tools (Jira, Slack, GitHub, Docker logs) and provides AI-powered incident detection, enrichment, and response. Built with FastAPI + Celery + PostgreSQL + Qdrant + LangChain.

---

## Architecture
- **API**: FastAPI (`apps/api/`) — REST endpoints, auth middleware, DB models, routers
- **Worker**: Celery (`apps/worker/`) — background tasks, Beat schedules
- **DB**: PostgreSQL (schema: `opslens`) — SQLAlchemy async ORM
- **Vector DB**: Qdrant — embeddings for RAG search
- **Cache/Queue**: Redis — Celery broker + result backend
- **Auth**: Clerk / Auth0 JWT (RS256)

---

## All Features Built

### 1. Multi-LLM Provider Support
- **File**: `apps/api/config.py`, `apps/api/services/rag_service.py`
- **Providers**: `openai` (GPT-4o + embeddings), `ollama` (local Llama 3.1), `claude` (Claude + OpenAI embeddings)
- **Config**: `LLM_PROVIDER=openai|ollama|claude`
- Claude uses OpenAI embeddings (Anthropic has no embeddings API)

### 2. RAG Chat
- **Files**: `apps/api/routers/rag.py`, `apps/api/services/rag_service.py`
- **Endpoints**: `POST /api/v1/chat/sessions`, `POST /api/v1/chat/sessions/{id}/messages`
- Ask questions across all connected sources (Jira, Slack, GitHub, docs)

### 3. Data Ingestion (Airbyte)
- **File**: `apps/api/routers/ingestion.py`
- **Endpoints**: `POST /api/v1/integrations/connect`, `GET /api/v1/integrations`

### 4. Insights
- **File**: `apps/api/routers/insights.py`, `apps/api/models/insight.py`
- **Endpoints**: `GET /api/v1/insights`, `POST /api/v1/insights/generate`

### 5. Alert Rules & History
- **File**: `apps/api/routers/alerts.py`, `apps/api/models/alert.py`
- **Endpoints**: `GET/POST/PATCH/DELETE /api/v1/alerts/rules`, `GET /api/v1/alerts/history`
- **Manual trigger**: `POST /api/v1/alerts/logs/scan`
- **Scan history**: `GET /api/v1/alerts/logs/history`

### 6. Two-Tier Log Monitoring
**Tier 1 — Fast Alert (every 5 min)**
- **File**: `apps/worker/tasks/log_fast_alert.py`
- **Task**: `logs.fast_scan` (Beat: every 5 min)
- Scans Docker containers + log files for ERROR/EXCEPTION/CRITICAL
- Fires Slack alert in < 2 min (no LLM delay)
- Dispatches enrichment + RRT brief async

**Tier 2 — Hourly LLM Digest**
- **File**: `apps/worker/tasks/log_scanner.py`
- **Task**: `logs.scan_and_report` (Beat: every 60 min)
- LLM summarises all issues from past hour, sends digest to Slack/email

### 7. Known Issue Suppression
- **Files**: `apps/api/models/log_ops.py` (KnownIssue), `apps/api/routers/log_ops.py`
- **Endpoints**: `GET/POST/PATCH/DELETE /api/v1/log-ops/known-issues`
- Suppression by: signature hash, regex/keyword pattern, snooze expiry, Jira ticket linkage

### 8. Team-Based Alert Routing
- **Files**: `apps/api/models/log_ops.py` (AlertRoutingRule), `apps/api/routers/log_ops.py`
- **Endpoints**: `GET/POST/PATCH/DELETE /api/v1/log-ops/routing-rules`
- **Test endpoint**: `POST /api/v1/log-ops/routing-rules/test`
- Priority-ordered rules, fan-out to multiple teams, stop_on_match

### 9. RAG Enrichment on Exceptions
- **File**: `apps/worker/tasks/log_fast_alert.py` (`_enrich_with_rag`)
- **Task**: `logs.enrich_and_alert`
- Embeds exception → Qdrant search (jira/slack/github filter) → returns related context
- LLM 2-3 sentence diagnosis → enriched Slack message

### 10. RRT Brief (Rapid Response Team Briefing)
- **Files**: `apps/api/models/rrt_brief.py`, `apps/worker/tasks/rrt_briefing.py`, `apps/api/routers/rrt_briefs.py`
- **Endpoints**: `GET /api/v1/rrt-briefs`, `GET /api/v1/rrt-briefs/{id}`, `PATCH /api/v1/rrt-briefs/{id}`, `POST /api/v1/rrt-briefs/{id}/update`
- **Task**: `rrt.generate_brief`
- LLM generates: title, what_happened, impact, suspected_cause, next_actions (JSON)
- Slack Block Kit delivery with full incident context
- Status lifecycle: open → investigating → resolved

### 12. Manager Dashboard
- **File**: `apps/api/routers/manager_dashboard.py`
- **Prefix**: `GET /api/v1/manager/`
- **Endpoints**:
  - `GET /summary` — rolling KPI snapshot: total/resolved/open incidents, MTTR, deploy count, delta vs previous period
  - `GET /delivery-risk` — open RRT Briefs enriched with deploy count 24h before each incident; Change Failure Rate (last 30d)
  - `GET /blocked-initiatives` — Jira issues stuck in same status > N days (default 3d)
  - `GET /deployment-frequency` — deploys per service per day over rolling window
  - `GET /team-health` — per-team: open incidents, resolved, MTTR, alerts fired, top service
- **Auth**: requires role `manager|admin|viewer`

### 13. PagerDuty Integration
- **File**: `apps/worker/tasks/pagerduty.py`
- **Functions**: `fire_pagerduty_alert`, `resolve_pagerduty_alert`, `acknowledge_pagerduty_alert`, `dispatch_pagerduty_alerts`, `make_dedup_key`
- **API**: PagerDuty Events API v2 (`https://events.pagerduty.com/v2/enqueue`)
- **Wire-up**: `log_fast_alert.py` Step 4b calls `dispatch_pagerduty_alerts` after timeline write
- **Routing**: `AlertRoutingRule.pagerduty_key` populated per-team; `_resolve_routing_async` now returns `pagerduty_key` in target dict
- **Dedup**: `make_dedup_key(tenant_id, error_signature)` → SHA-256 → 40-char hex — same error clusters to same PD incident
- **Config**: `PAGERDUTY_ROUTING_KEY` (global fallback); per-team keys on routing rules

### 14. SAML SSO + SCIM + RBAC + Audit Logs
- **Models**: `apps/api/models/saml.py` (SAMLConfig, SCIMConfig), `apps/api/models/rbac.py` (RoleAssignment, APIKey), `apps/api/models/audit.py` (AuditLog)
- **Router**: `apps/api/routers/enterprise.py` (prefix: `/api/v1`)
- **SAML Endpoints**: `GET /auth/saml/metadata`, `POST /auth/saml/acs`, `GET/POST/DELETE /auth/saml/config`
- **SCIM Endpoints**: `POST /auth/scim/config`, `GET/POST /scim/v2/Users`, `GET/DELETE /scim/v2/Users/{id}`
- **RBAC Endpoints**: `GET/POST /rbac/roles`, `DELETE /rbac/roles/{id}`, `GET/POST /rbac/api-keys`, `DELETE /rbac/api-keys/{id}`
- **Audit Endpoint**: `GET /audit` — paginated, filterable by actor/resource/action/time
- **API Keys**: `sk-oplen_` prefix, SHA-256 hash stored, raw key returned once; validated in JWTAuthMiddleware
- **RBAC Roles**: `admin`, `manager`, `engineer`, `viewer`; `require_roles()` dependency added to `middleware.py`
- **AuditLog**: append-only; written by all write operations; fields: actor_id, actor_role, resource, action, before, after, ip_address, request_id
- **Config**: `SAML_SP_ENTITY_ID`, `SAML_SP_BASE_URL`

### 15. Retention Controls
- **Model**: `apps/api/models/retention.py` (RetentionPolicy)
- **Celery Task**: `apps/worker/tasks/retention.py` (`retention.run_cleanup`) — Beat: daily 02:00 UTC
- **Router**: `apps/api/routers/retention.py` (prefix: `/api/v1/retention`)
- **Endpoints**: `GET /`, `POST /` (upsert policy), `POST /run` (manual trigger), `GET /stats` (projected deletions)
- **Categories**: log_scan_history, timeline_events, rrt_briefs (resolved only), audit_logs, chat_sessions, insights, qdrant_embeddings
- **Archive**: optional S3 JSONL export before deletion (requires `AWS_*` + `RETENTION_ARCHIVE_BUCKET`)
- **Config**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, `RETENTION_ARCHIVE_BUCKET`

### 11. Delivery Risk Timeline
- **Files**: `apps/api/models/timeline.py` (TimelineEvent), `apps/api/routers/timeline.py`
- **Endpoints**:
  - `GET /api/v1/timeline` — query events (time window, source_type, service, team)
  - `GET /api/v1/timeline/around/{brief_id}` — ±N min around an RRT Brief (the "what changed?" view)
  - `POST /api/v1/timeline/events` — manual event
  - `POST /api/v1/timeline/webhooks/github` — GitHub webhook receiver
  - `POST /api/v1/timeline/webhooks/jira` — Jira webhook receiver
- **Source types**: github_pr, github_commit, github_deploy, jira_issue, log_anomaly, rrt_brief, manual
- Timeline events auto-injected into RRT briefs (LLM prompt + Slack "What Changed" section)

---

## Key Configuration Variables (.env)

```
LLM_PROVIDER=openai|ollama|claude
ANTHROPIC_API_KEY=
ANTHROPIC_CHAT_MODEL=claude-opus-4-6
OPENAI_API_KEY=
DATABASE_URL=postgresql+asyncpg://...
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0
LOG_FAST_ALERT_ENABLED=true
LOG_FAST_ALERT_SLACK_WEBHOOK=
LOG_SCAN_SLACK_WEBHOOK=
GITHUB_WEBHOOK_SECRET=
JIRA_WEBHOOK_TOKEN=
DEFAULT_TENANT_ID=default
```

---

## Incident Response Flow (End-to-End)
```
T+0:   Exception fires (Docker log or file)
T+2m:  Fast scan fires → raw Slack alert to matched team(s)
       + TimelineEvent (log_anomaly) written to DB
T+3m:  enrich_and_alert: RAG search → enriched Slack with Jira/Slack/GitHub context
T+4m:  generate_rrt_brief:
         1. Fetch timeline events (last 60 min) — PRs, deploys, Jira transitions
         2. LLM generates: title, what_happened, impact, suspected_cause, next_actions
         3. Slack Block Kit brief with "What Changed Before" section
         4. Saved to DB with open status + lifecycle management
T+Nm:  Engineers update brief status via PATCH /api/v1/rrt-briefs/{id}
       Status changes posted to Slack thread
```

---

## Files Created/Modified (Full List)
- `apps/api/config.py` — LLM providers, log scan settings, timeline webhook settings
- `apps/api/services/rag_service.py` — Claude provider branch
- `apps/api/models/log_ops.py` — KnownIssue, AlertRoutingRule
- `apps/api/models/rrt_brief.py` — RRTBrief
- `apps/api/models/timeline.py` — TimelineEvent (NEW)
- `apps/api/routers/log_ops.py` — known issues + routing CRUD
- `apps/api/routers/rrt_briefs.py` — RRT brief API
- `apps/api/routers/timeline.py` — timeline query + webhooks (NEW)
- `apps/api/routers/alerts.py` — manual scan trigger + history
- `apps/api/main.py` — all routers registered
- `apps/worker/tasks/log_scanner.py` — hourly digest task
- `apps/worker/tasks/log_fast_alert.py` — fast scan + enrichment + timeline write + PagerDuty dispatch
- `apps/worker/tasks/rrt_briefing.py` — RRT brief generation + timeline context
- `apps/worker/tasks/pagerduty.py` — PagerDuty Events API v2 helpers (NEW)
- `apps/worker/tasks/retention.py` — daily data retention cleanup task (NEW)
- `apps/worker/celery_app.py` — Beat schedules (includes retention daily at 02:00 UTC)
- `apps/worker/models/log_scan.py` — LogScanHistory
- `apps/api/routers/manager_dashboard.py` — manager KPI aggregation (NEW)
- `apps/api/routers/enterprise.py` — SAML + SCIM + RBAC + Audit (NEW)
- `apps/api/routers/retention.py` — retention policy API (NEW)
- `apps/api/models/audit.py` — AuditLog (NEW)
- `apps/api/models/rbac.py` — RoleAssignment, APIKey (NEW)
- `apps/api/models/saml.py` — SAMLConfig, SCIMConfig (NEW)
- `apps/api/models/retention.py` — RetentionPolicy (NEW)
- `apps/api/auth/middleware.py` — added require_roles(), API key auth, SCIM bypass
- `requirements.txt` — langchain-anthropic, anthropic
- `.env` — all new vars documented

---

## Next Roadmap Items
1. **CODEOWNERS integration** — auto-route alerts to GitHub team owners based on `.github/CODEOWNERS`
2. **Manager Dashboard UI** — React frontend consuming `/api/v1/manager/*` endpoints
3. **python3-saml** — wire full SAML assertion validation in `saml_acs` (currently stub)
4. **SCIM Groups** — full SCIM v2 Groups endpoints (list/create/update/delete)
5. **Cost & token tracking** — track LLM token spend per tenant per model
