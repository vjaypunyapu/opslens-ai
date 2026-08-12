# OpsLens AI — Codebase Skill & Memory

> This file is the single source of truth for any AI session working on OpsLens AI.
> Read this before touching any code. It covers architecture, conventions, gotchas,
> and the multi-tenancy rules that must never be violated.

---

## What Is OpsLens AI?

A **multi-tenant SaaS Operational Intelligence Copilot** for engineering and product teams.
It ingests data from 10+ sources (GitHub, Jira, Slack, Zendesk, HubSpot, Google Drive,
Elasticsearch, Datadog, CloudWatch, Splunk, Azure Monitor, GCP Logging), detects
operational patterns using AI, surfaces proactive insights, and enables conversational
Q&A over the team's full operational context.

---

## Repository Layout

```
opslens-ai/
├── apps/
│   ├── api/                  FastAPI backend (Python 3.11+)
│   │   ├── main.py           App factory — registers all routers + middleware
│   │   ├── config.py         Pydantic Settings (all env vars, @lru_cache singleton)
│   │   ├── auth/
│   │   │   ├── middleware.py JWTAuthMiddleware (RS256, sets request.state fields)
│   │   │   └── dependencies.py  TenantContext, require_admin/member/viewer
│   │   ├── db/
│   │   │   ├── session.py    AsyncEngine, AsyncSessionFactory, tenant_select() helper
│   │   │   ├── models.py     ALL core ORM models (single file with 8 models)
│   │   │   ├── base.py       DeclarativeBase for standalone model files
│   │   │   └── schema.sql    Raw SQL schema (source of truth for migrations)
│   │   ├── models/           Thin re-export wrappers + extra standalone models
│   │   │   ├── alert.py      re-exports AlertHistory, AlertRule
│   │   │   ├── audit.py      AuditLog (standalone, schema=opslens)
│   │   │   ├── auth_providers.py OIDCConfig, LDAPConfig
│   │   │   ├── billing.py    UsageEvent, BillingSubscription
│   │   │   ├── log_ops.py    KnownIssue, AlertRoutingRule
│   │   │   ├── rbac.py       RoleAssignment, APIKey (VALID_ROLES, ROLE_RANK)
│   │   │   ├── retention.py  RetentionPolicy
│   │   │   ├── rrt_brief.py  RRTBrief
│   │   │   ├── saml.py       SAMLConfig, SCIMConfig
│   │   │   └── timeline.py   TimelineEvent
│   │   ├── routers/          One file per endpoint group (17 routers)
│   │   ├── services/         Business logic layer
│   │   │   ├── planner.py    Legacy single-agent: plan→retrieve→generate→validate
│   │   │   ├── rag_service.py  RagService (now delegates to agent graph)
│   │   │   ├── hybrid_retriever.py  BM25 + Qdrant dense + Cohere rerank + RRF
│   │   │   ├── validator.py  Auditor + Gatekeeper + Strategist (3 parallel LLM nodes)
│   │   │   ├── guardrails.py Input/output safety layer (injection, PII, topic scope)
│   │   │   ├── permissions.py  get_allowed_sources() for RBAC source filtering
│   │   │   └── telemetry.py  In-memory trace store (latency, token counts)
│   │   └── agents/           Multi-agent system (v3 — LangGraph)
│   │       ├── state.py      AgentState TypedDict
│   │       ├── tools.py      Async tool library (retrieve, insights, alerts, incidents)
│   │       └── graph.py      LangGraph graph: supervisor→dispatcher→synthesizer
│   │
│   ├── worker/               Celery workers
│   │   ├── celery_app.py     App factory + Beat schedule (8 periodic tasks)
│   │   ├── db.py             NullPool AsyncSession (required for fork-based workers)
│   │   ├── async_utils.py    run_async() — bridges sync Celery tasks to async code
│   │   ├── structural_parser.py  Document chunking (code/table/prose-aware)
│   │   └── tasks/
│   │       ├── ingestion.py  3-stage pipeline: normalize→dedupe→chunk→embed→upsert
│   │       ├── insight_engine.py  5 AI detectors (BaseDetector ABC)
│   │       ├── insight_runner.py  LLM-based pattern analysis runner
│   │       ├── log_fast_alert.py  Tier-1 (<2min raw) + Tier-2 (RAG-enriched) alerts
│   │       ├── log_scanner.py     Hourly LLM digest of all issues
│   │       ├── log_source_poller.py  Polls observability APIs (Datadog, CloudWatch, etc.)
│   │       ├── rrt_briefing.py    RRT brief generation with timeline context
│   │       ├── alert_runner.py    Rule evaluator + dispatch
│   │       ├── escalation.py      Alert escalation logic
│   │       ├── pagerduty.py       PagerDuty Events API v2
│   │       ├── retention.py       Data retention cleanup
│   │       └── master.py          Fan-out Celery tasks for Beat schedule
│   │
│   └── web/                  Next.js 14 frontend (TypeScript, Tailwind, Clerk auth)
│       └── src/app/          App router — pages for chat, alerts, insights, settings
│
├── migrations/               Alembic (env.py + versions/)
├── infra/docker/             docker-compose.yml (Postgres, Redis, Qdrant, Airbyte)
├── tests/                    pytest + pytest-asyncio
├── Makefile                  Dev commands (make dev, make worker, make test, etc.)
├── railway.toml              Production deployment (4 services via $RAILWAY_SERVICE_NAME)
├── requirements.txt          Python deps
└── pyproject.toml            Ruff, mypy, pytest, coverage config
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI 0.115+, Uvicorn, Python 3.11+ |
| Frontend | Next.js 14.2.5, React 18, TypeScript, Tailwind CSS, Clerk auth, Radix UI |
| Database | PostgreSQL 15, SQLAlchemy async (`asyncpg`), Alembic migrations |
| Vector store | Qdrant — per-tenant collections, 1536-dim cosine similarity |
| LLM / agents | LangChain LCEL, LangGraph ≥ 0.2, OpenAI / Anthropic / Ollama |
| Embeddings | `text-embedding-3-small` (1536 dims, always OpenAI regardless of LLM_PROVIDER) |
| Task queue | Celery + Redis (broker + result backend) + Celery Beat scheduler |
| Auth | JWT RS256 (Clerk/Auth0), SAML 2.0, OIDC, LDAP, RBAC |
| Notifications | Slack Block Kit, Microsoft Teams (connector/workflow), PagerDuty, SendGrid, Resend |
| ETL | Airbyte + direct polling (Datadog, CloudWatch, Splunk, GCP, Azure Monitor) |
| Observability | Sentry, structlog (JSON), LangSmith (optional) |

---

## ⚠️ Multi-Tenancy — Critical Rules

**Every DB query against tenant-scoped tables MUST include `tenant_id`.**
A missing filter is a cross-tenant data leak and a critical compliance failure.

### Enforcement layers

1. **JWT middleware** (`auth/middleware.py`) — extracts `org_id` or `sub` from JWT,
   sets `request.state.tenant_id`. Also sets `user_id`, `email`, `company_name`.

2. **`TenantContext`** (`auth/dependencies.py`) — dataclass injected via `Depends`.
   Always use `ctx.tenant_id` (str) or `ctx.tenant_uuid` (UUID) in DB queries.

3. **`tenant_select()` helper** (`db/session.py`) — preferred query pattern:
   ```python
   q = tenant_select(Incident, ctx.tenant_id)
   # expands to: sa.select(Incident).where(Incident.tenant_id == tenant_id)
   ```

4. **Qdrant** — per-tenant collections named `opslens_{tenant_id}`.
   Also uses payload filters `{"tenant_id": tenant_id}` for extra safety.

5. **Auto-provisioning** — first JWT login auto-creates `Tenant` + `User` rows.
   First user in a tenant gets `role='admin'`. Subsequent users require a valid invite.

### RBAC roles (in priority order)

| Role | Access |
|------|--------|
| `admin` | Full read/write, manage users, SSO, SCIM, routing rules |
| `manager` | Read-only all data + manager dashboard endpoints |
| `engineer` | Read/write operational resources (alerts, routing, RRT briefs) |
| `viewer` | Read-only all operational resources |

Auth dependencies: `require_admin`, `require_member`, `require_viewer` (from `auth/dependencies.py`).
Role hierarchy in `_ROLE_RANK`: `viewer=0`, `member=1`, `admin=2`.

### Domain allowlist & invite-only

- `Tenant.allowed_email_domains` (JSONB list) — if non-empty, blocks new users from other domains.
- All workspaces are **invite-only** by default after the first user.
- Invites stored in `PendingInvite` model (email, role, expires_at, accepted_at).

---

## Core ORM Models

All in `apps/api/db/models.py` (schema: `opslens`). All use UUIDs as PKs.

| Model | Table | Key Fields |
|-------|-------|-----------|
| `Tenant` | `opslens.tenants` | `id`, `name`, `slug`, `plan`, `settings`, `allowed_email_domains` |
| `User` | `opslens.users` | `id`, `tenant_id` (FK), `external_id` (Clerk sub), `email`, `role` |
| `Integration` | `opslens.integrations` | `id`, `tenant_id`, `source_type`, `credentials` (AES-256 encrypted JSONB), `status`, `last_synced_at` |
| `CanonicalDocument` | `opslens.canonical_documents` | `id`, `tenant_id`, `source_type`, `source_id`, `content_hash`, `title`, `content`, `doc_metadata` (JSONB), `embedding_status` |
| `Insight` | `opslens.insights` | `id`, `tenant_id`, `insight_type`, `title`, `summary`, `magnitude`, `status`, `source_types`, `evidence` |
| `AlertRule` | `opslens.alert_rules` | `id`, `tenant_id`, `name`, `conditions` (JSONB), `channels` (JSONB), `is_active`, `cooldown_minutes` |
| `AlertHistory` | `opslens.alert_history` | `id`, `tenant_id`, `rule_id`, `triggered_at`, `payload` |
| `ChatSession` | `opslens.chat_sessions` | `id`, `tenant_id`, `user_id`, `title`, `created_at` |
| `ChatMessage` | `opslens.chat_messages` | `id`, `session_id`, `role`, `content`, `sources` (JSONB), `feedback` (SMALLINT), `latency_ms` |

### Standalone model files (use `apps/api/db/base.py` Base)

| Model | File | Purpose |
|-------|------|---------|
| `AuditLog` | `models/audit.py` | SOC2 append-only event ledger |
| `KnownIssue` | `models/log_ops.py` | Alert suppression (by hash, regex, or Jira ticket) |
| `AlertRoutingRule` | `models/log_ops.py` | Routes errors to teams (keywords/regex → Slack/email/PagerDuty) |
| `RRTBrief` | `models/rrt_brief.py` | Incident artifacts (what/impact/cause/next_actions) |
| `TimelineEvent` | `models/timeline.py` | Unified event feed (PR/commit/deploy/anomaly/manual) |
| `RetentionPolicy` | `models/retention.py` | Per-tenant data cleanup thresholds (days) |
| `SAMLConfig` | `models/saml.py` | Per-tenant SAML 2.0 IdP config |
| `SCIMConfig` | `models/saml.py` | Per-tenant SCIM 2.0 provisioning config |
| `OIDCConfig` | `models/auth_providers.py` | Per-tenant OIDC/OAuth2 SSO config |
| `LDAPConfig` | `models/auth_providers.py` | Per-tenant LDAP/AD config |
| `RoleAssignment` | `models/rbac.py` | Explicit per-tenant role overrides |
| `APIKey` | `models/rbac.py` | Service-account keys (SHA-256 stored, never plaintext) |
| `UsageEvent` | `models/billing.py` | Billable action ledger (Stripe metering) |
| `BillingSubscription` | `models/billing.py` | Stripe subscription state per tenant |

### `CanonicalDocument` — source_type values

```
github, jira, slack, zendesk, hubspot, gdrive,
elasticsearch, datadog, cloudwatch, splunk, azuremonitor, gcplogging, railway
```

---

## Database Sessions

```python
# In FastAPI routes (per-request session via Depends)
from ..db.session import get_db
async def my_endpoint(db = Depends(get_db)): ...

# In services/workers outside request context
from ..db.session import AsyncSession   # context manager
async with AsyncSession() as db:
    result = await db.execute(...)

# In Celery workers (NullPool — required for fork-based workers)
from apps.worker.db import AsyncSession   # different import! uses NullPool
```

**NEVER import `apps.api.db.session.AsyncSession` in Celery tasks** — use `apps.worker.db.AsyncSession` instead (NullPool, no connection sharing across fork).

---

## Multi-Agent System (v3 — current)

### Architecture

```
User Query → [Supervisor] → [Dispatcher (parallel)] → [Synthesizer] → SSE stream
                                ├── Research Agent   (hybrid RAG)
                                ├── Insight Agent    (pattern detection)
                                ├── Alert Agent      (active alerts)
                                └── Incident Agent   (RRT briefs + timeline)
```

### Files

| File | Purpose |
|------|---------|
| `agents/state.py` | `AgentState` TypedDict — shared state across all graph nodes |
| `agents/tools.py` | Async tool library: `retrieve_documents`, `get_recent_insights`, `run_insight_detector_now`, `get_active_alerts`, `get_alert_summary`, `get_recent_rrt_briefs`, `get_incident_timeline` |
| `agents/graph.py` | All 5 node functions + LangGraph `StateGraph` compilation + `AgentGraph` class |

### Routing rules (supervisor LLM decides)

- `research` — factual questions, "what is X", doc retrieval
- `insight` — patterns, trends, anomalies, "any complaint spikes"
- `alert` — "active alerts", "what's firing", severity summaries
- `incident` — "what happened", outage briefs, deployment timelines
- Multiple agents — parallel when query spans domains

### SSE event types (in order)

```jsonc
{"type": "agent",   "data": "research"}         // agent activated (v3 new)
{"type": "token",   "data": "word "}             // answer token
{"type": "sources", "data": [...]}               // citations
{"type": "trace",   "data": [...]}               // audit trail (v3 new)
{"type": "done",    "latency_ms": 1240, "agents": [...]}
{"type": "error",   "message": "..."}            // triggers fallback
```

### Guardrails (applied before and after every LLM call)

`services/guardrails.py` — all regex-based, zero LLM cost:

```
check_input(question) — runs in supervisor_node BEFORE any LLM call
  1. Length cap         → truncate to INPUT_MAX_CHARS (default 4000, env-configurable)
  2. Prompt injection   → 8 hard-block patterns (ignore previous instructions, DAN, etc.)
  3. Jailbreak soft     → block if ≥3 of 5 soft signals match
  4. Topic scope        → block clearly off-topic questions (movies, recipes, sports, etc.)

check_output(answer) + redact_output(answer) — runs in synthesizer_node AFTER LLM
  - PII redaction       → email, phone, credit card, SSN, AWS keys, private IPs
                          (skips fenced code blocks, logs when redaction occurs)
  - Content filter      → blocks empty or pure-error answers
```

**Topic scope enforcement is two-layer:**
1. Fast regex gate (`check_topic_scope`) — catches obvious off-topic before any LLM call
2. Supervisor system prompt — explicit in-scope/out-of-scope definitions; returns `{"scope": "out_of_scope"}` which short-circuits the entire graph (no agent calls, no synthesizer)

Out-of-scope message is consistent across both layers.

### Fallback chain

`agent_graph.stream()` → fails → `planner.plan_and_answer()` → fails → error event

---

## RAG Pipeline (planner.py — used by Research Agent)

```
Plan (gpt-4o-mini, JSON output) → Retrieve (parallel hybrid) → Generate → Validate
     ↑                                                                        |
     └──────────── retry with missing_queries from Gatekeeper ───────────────┘
                   (max MAX_ITER=2 retries)
```

### Hybrid retrieval (`hybrid_retriever.py`)

1. BM25 sparse search (`rank-bm25`)
2. Qdrant dense search (`text-embedding-3-small`, tenant-scoped collection)
3. Cohere Rerank (optional, env-gated)
4. RRF (Reciprocal Rank Fusion) merge + dedup
5. Source diversity cap: no single `source_type` > 40% of results

### Validation nodes (`validator.py`) — all run in parallel

| Node | Role | Triggers user warning? |
|------|------|----------------------|
| Auditor | Grounding — every claim supported by context? | **Yes** (⚠️ disclaimer) |
| Gatekeeper | Completeness — all parts of question answered? | No (advisory, triggers retry) |
| Strategist | Logic — sound reasoning, no contradictions? | No (advisory only) |

### LangSmith coverage

Every direct `openai.AsyncOpenAI` and `anthropic.AsyncAnthropic` call is instrumented with explicit LangSmith runs (not just LangChain-wrapped calls). Covered nodes:

- `supervisor` (`_llm_json` in `graph.py`)
- `insight_agent`, `alert_agent`, `incident_agent`, `synthesizer` (`_llm_text` in `graph.py`)
- `auditor`, `gatekeeper`, `strategist` (`_call_validator` in `validator.py`)

Enable with `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY`. All runs appear in LangSmith under `LANGCHAIN_PROJECT`.

### RBAC isolation (verified airtight)

| Layer | Enforcement |
|-------|------------|
| Chat session ownership | `WHERE id=:id AND tenant_id=:tenant_id` — 404 if mismatch |
| Qdrant retrieval | Per-tenant collection (`opslens_{tenant_id}`) — physical separation |
| BM25 SQL search | `WHERE tenant_id=:tenant_id::uuid` first predicate on every branch |
| Aggregate counts | `_aggregate_count` now respects `allowed_sources` (fixed PR #26) |
| `astream_planned_response_live` | Now accepts + threads `allowed_sources` (fixed PR #26) |

### LLM provider routing

Config: `LLM_PROVIDER` env var (`openai` | `claude` | `ollama`)

| Provider | Chat model | Embeddings |
|----------|-----------|-----------|
| `openai` | `gpt-4o` (full) / `gpt-4o-mini` (simple) | `text-embedding-3-small` |
| `claude` | `claude-opus-4-6` (or `claude-sonnet-4-6`) | OpenAI (Anthropic has no embedding API) |
| `ollama` | `llama3.1` | `nomic-embed-text` (768 dims local) |

**Model tier routing**: supervisor sets `model_tier="mini"` for simple/single-source queries → cheaper. Complex/multi-source → `"full"`.

---

## Celery Workers & Beat Schedule

### Queues
```
ingestion   — document processing (normalize → chunk → embed → Qdrant upsert)
insights    — insight detector runs (hourly per tenant)
alerts      — rule evaluation + dispatch
logs        — log scanning, fast alert, log source polling
rrt         — RRT brief generation
```

### Beat schedule (periodic tasks)

| Task | Schedule | Purpose |
|------|----------|---------|
| `insights.run_all_tenants` | Every hour (`:00`) | Run 5 detectors for all tenants |
| `alerts.evaluate_all_tenants` | Every 15 min | Evaluate alert rules |
| `ingestion.process_all_staging` | Every 5 min | Process staging queue |
| `logs.scan_and_report` | Configurable (`LOG_SCAN_CRON_MINUTES=60`) | Hourly LLM digest |
| `logs.fast_scan_all_tenants` | Every 5 min (`LOG_FAST_ALERT_CRON_MINUTES`) | Fast exception alerting |
| `logs.poll_all_log_sources` | Every 5 min | Poll observability APIs |
| `retention.run_cleanup` | Daily 02:00 UTC | Data retention cleanup |
| `ingestion.retry_pending_embeddings` | Every 30 min | Retry failed embeddings |
| `ingestion.sync_all_contextual_sources` | Daily 03:00 UTC (configurable) | Re-sync contextual sources |

### Two-tier alerting (`log_fast_alert.py`)

- **Tier 1** (<2 min): Raw error notification → Slack/Teams immediately, no LLM
- **Tier 2** (async): RAG-enriched diagnosis + RRT brief → routes to team via AlertRoutingRule
- Dedup: `LOG_FAST_ALERT_COOLDOWN_MINUTES=10` (in-process) + `LOG_INCIDENT_COOLDOWN_HOURS=0.25` (DB)

---

## Ingestion Pipeline (`worker/tasks/ingestion.py`)

```
RawRecord (IngestionQueue table)
    ↓ normalize (source-specific)
    ↓ deduplicate (content_hash, ON CONFLICT upsert to canonical_documents)
    ↓ structural parse (code blocks atomic, tables atomic, prose with heading breadcrumbs)
    ↓ HyDE (gpt-4o-mini generates hypothetical questions per chunk at ingest time)
    ↓ embed (text-embedding-3-small, batch=100)
    ↓ upsert to Qdrant (tenant collection, payload: {tenant_id, source_type, title, url, ...})
```

**HyDE is at ingest time, not query time** — zero query latency overhead.

### Source types supported
- **Contextual** (full ingest): `github`, `jira`, `slack`, `zendesk`, `hubspot`, `gdrive`
- **Log/observability** (filtered by `INGEST_LOG_MIN_LEVEL=WARNING`): `elasticsearch`, `datadog`, `cloudwatch`, `splunk`, `azuremonitor`, `gcplogging`, `railway`

---

## Insight Detectors (`worker/tasks/insight_engine.py`)

All extend `BaseDetector(ABC)`. Run hourly via Celery Beat. Also triggerable on-demand via Insight Agent.

| Detector | `insight_type` | Sources | Look-back |
|----------|---------------|---------|-----------|
| `ComplaintSpikeDetector` | `complaint_spike` | zendesk, slack | 7 days |
| `FeatureTrendDetector` | `feature_trend` | jira, zendesk, slack | 30 days |
| `ReleaseCorrelationDetector` | `release_correlation` | github, jira | 14 days |
| `EngBottleneckDetector` | `eng_bottleneck` | jira | ongoing (stale > 5 days) |
| `ChurnRiskDetector` | `churn_risk` | zendesk, hubspot | 30 days |

Dedup: same `title` + same `tenant_id` within 24h is skipped.

---

## API Endpoints (routers/)

All prefixed `/api/v1/`. Auth dependency shown in parentheses.

| Router | Prefix | Key Endpoints |
|--------|--------|--------------|
| `rag.py` | `/chat` | `POST /sessions`, `GET /sessions`, `POST /sessions/{id}/query` (SSE), `DELETE /sessions/{id}`, `POST /feedback`, `GET /metrics` |
| `agents.py` | `/agents` | `GET /status`, `POST /run`, `POST /stream` |
| `insights.py` | `/insights` | `GET /`, `GET /{id}`, `POST /{id}/snooze`, `DELETE /{id}` |
| `alerts.py` | `/alerts` | `GET /`, `POST /`, `PATCH /{id}`, `DELETE /{id}` |
| `ingestion.py` | `/ingestion` | `POST /` (webhook ingest), `GET /status`, `POST /sync` |
| `rrt_briefs.py` | `/rrt-briefs` | `GET /`, `GET /{id}`, `PATCH /{id}/status` |
| `timeline.py` | `/timeline` | `GET /`, `POST /events` (manual events) |
| `incidents.py` | `/incidents` | `GET /`, `GET /{id}` |
| `log_ops.py` | `/log-ops` | Known issues, alert routing rules CRUD |
| `admin.py` | `/admin` | Users, teams, RBAC, invites, domain allowlist |
| `users.py` | `/users` | `GET /me`, `PATCH /me` |
| `settings.py` | `/settings` | Integration config, webhook setup |
| `analytics.py` | `/analytics` | Usage stats, cost breakdown |
| `billing.py` | `/billing` | Subscription, usage events |
| `enterprise.py` | `/enterprise` | SAML, SCIM, OIDC, LDAP config |
| `platform.py` | `/platform` | Founder panel (platform admin only) |
| `dashboard.py` | `/` | Dashboard summary endpoint |

---

## Key Configuration (config.py — all env vars)

### Required in production
```bash
DATABASE_URL          # postgresql+asyncpg://... (Railway injects postgres://, auto-converted)
JWT_PUBLIC_KEY        # RSA PEM public key (or use JWT_PUBLIC_KEY_URL for JWKS)
OPENAI_API_KEY        # Always needed (embeddings use OpenAI even with LLM_PROVIDER=claude)
```

### LLM Provider selection
```bash
LLM_PROVIDER=openai   # gpt-4o + text-embedding-3-small (default)
LLM_PROVIDER=claude   # claude-opus-4-6 + OpenAI embeddings
LLM_PROVIDER=ollama   # llama3.1 + nomic-embed-text (fully private)
ANTHROPIC_CHAT_MODEL=claude-sonnet-4-6   # cheaper Claude option
```

### Key operational settings
```bash
QDRANT_URL                        # default: http://localhost:6333
QDRANT_COLLECTION_PREFIX=opslens_ # collections: opslens_{tenant_id}
REDIS_URL                         # Celery broker/backend
NOTIFICATION_PROVIDER=slack       # or "teams"
LOG_FAST_ALERT_THRESHOLD=3        # min errors to fire alert
LOG_FAST_ALERT_COOLDOWN_MINUTES=10
INGEST_LOG_MIN_LEVEL=WARNING      # filters DEBUG/INFO from log sources
PLATFORM_ADMIN_EMAILS             # comma-separated, grants /platform/* access
INPUT_MAX_CHARS=4000              # max user question length before truncation
LANGCHAIN_TRACING_V2=true         # enable LangSmith tracing
LANGCHAIN_API_KEY=                # LangSmith API key
LANGCHAIN_PROJECT=opslens         # LangSmith project name
```

---

## Authentication Flow

```
Client → Bearer <JWT (RS256)>
           ↓
JWTAuthMiddleware (auth/middleware.py)
  - Verify signature using JWT_PUBLIC_KEY or JWKS at JWT_PUBLIC_KEY_URL
  - Extract: org_id (→ tenant_id), sub (→ user_id), email, company_name
  - Set on request.state.*
           ↓
require_member / require_admin / require_viewer (auth/dependencies.py)
  - _provision_tenant(): auto-create Tenant row if first login
  - _provision_user(): auto-create User row; first user gets admin role
  - Invite-only: non-first users must have valid PendingInvite
  - Returns TenantContext(tenant_id, user_id, role, company_name)
```

### SSO flows (enterprise)
- **SAML 2.0**: `GET /auth/saml/login?tenant_id=...` → IdP → `POST /auth/saml/acs`
- **OIDC**: `GET /auth/oidc/login?tenant_id=...` → IdP → `GET /auth/oidc/callback`
- **LDAP**: `POST /auth/ldap/login` with credentials
- All SSO flows issue internal HS256 JWT, redirect to `{FRONTEND_URL}/auth/sso-callback?token=<jwt>`

---

## Deployment

### Local dev
```bash
make up        # Docker: Postgres + Redis + Qdrant + Airbyte
make install   # Python venv + deps
make dev       # FastAPI at :8000 (uvicorn --reload)
make worker    # Celery worker (all queues)
make beat      # Celery Beat scheduler
make test      # pytest with asyncio
```

### Production (Railway)
Single `railway.toml` dispatches 4 services by `$RAILWAY_SERVICE_NAME`:
- `api` → `uvicorn apps.api.main:app`
- `worker` → `celery -A apps.worker.celery_app worker`
- `beat` → `celery -A apps.worker.celery_app beat`
- `web` → `npm run build && npm start` (Next.js)

Alembic migrations run automatically on API startup via `lifespan` hook in `main.py`.

---

## Code Conventions & Gotchas

### Always do

```python
# 1. Use tenant_select() for all tenant-scoped queries
from ..db.session import tenant_select
q = tenant_select(MyModel, ctx.tenant_id)

# 2. Use ctx.tenant_uuid (UUID) for UUID FK columns, ctx.tenant_id (str) for String columns
ChatSession(tenant_id=ctx.tenant_uuid)  # UUID FK
Alert(tenant_id=ctx.tenant_id)          # String column

# 3. Use AsyncSession from the right place
# In FastAPI → from ..db.session import AsyncSession (connection pool)
# In Celery  → from apps.worker.db import AsyncSession (NullPool)

# 4. Encrypt credentials before storing in Integration.credentials
from ..utils.crypto import encrypt, decrypt
Integration(credentials=encrypt({"token": "..."}))

# 5. Record every write to AuditLog for compliance
from ..utils.audit import record_audit
await record_audit(db, tenant_id, actor_id, "alert_routing_rule", rule_id, "create")
```

### Never do

```python
# ❌ Missing tenant filter — cross-tenant data leak
sa.select(ChatSession)  # ALWAYS add .where(ChatSession.tenant_id == ...)

# ❌ Import API session in Celery tasks
from apps.api.db.session import AsyncSession  # in worker code → use apps.worker.db

# ❌ Store credentials in plaintext
Integration(credentials={"token": raw_token})  # encrypt first

# ❌ Hardcode model names — always read from settings
model = "gpt-4o"  # use settings.OPENAI_CHAT_MODEL

# ❌ Call detect_insight from a Celery task without run_async()
await detector.run(tenant_id)  # inside sync Celery task → use _run_async()
```

### Common patterns

```python
# Streaming SSE response
from fastapi.responses import StreamingResponse
async def event_gen():
    async for event in service.stream(...):
        yield f"data: {json.dumps(event)}\n\n"
return StreamingResponse(event_gen(), media_type="text/event-stream",
    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

# Celery async task bridge
from apps.worker.async_utils import run_async as _run_async
@shared_task(name="task.name", bind=True, max_retries=2, default_retry_delay=120)
def my_task(self, tenant_id: str):
    return _run_async(_my_async_fn(tenant_id))
```

---

## Qdrant Collection Naming

```python
collection_name = f"opslens_{tenant_id}"
# e.g. opslens_550e8400-e29b-41d4-a716-446655440000
```

Payload stored per vector:
```json
{"tenant_id": "...", "source_type": "github", "title": "...", "url": "...",
 "author": "...", "created_at": "...", "doc_id": "..."}
```

Filter in queries:
```python
Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))])
```

---

## Permissions & Source Access Control

`services/permissions.py` → `get_allowed_sources(user_id, tenant_id, db)`

Returns:
- `None` — admin, unrestricted access to all sources
- `[]` — no access at all
- `[{source_id, source_type, ...}]` — restricted to these sources

This is passed as `allowed_sources` to `hybrid_retrieve()` and `plan_and_answer()` to filter Qdrant results.

---

## Testing

```bash
make test                          # run all tests
pytest tests/ -v                   # verbose
pytest tests/test_insight_engine.py  # specific file
```

- Framework: `pytest` + `pytest-asyncio`
- Coverage target: 70% minimum (`pyproject.toml`)
- Linting: `ruff` (fast, replaces flake8+isort+black)
- Type checking: `mypy` (optional, not strict)

---

## Active Branch & PR

- **Feature branch**: `claude/lucid-noether-Zp2PP`
- **PR #21**: Multi-agent AI architecture (merged to main)
- **PR #26**: Guardrails, topic scope, LangSmith coverage, RBAC fixes (open)
- **Main branch**: `main`

---

## Quick Reference — Where to Find Things

| Task | Location |
|------|---------|
| Add a new API endpoint | Create/edit file in `apps/api/routers/`, register in `apps/api/main.py` |
| Add a new ORM model | Add to `apps/api/db/models.py` + create Alembic migration |
| Add a new Celery task | Add to `apps/worker/tasks/`, register in `celery_app.py` include list |
| Add a new insight detector | Subclass `BaseDetector` in `insight_engine.py`, add to `ALL_DETECTORS` |
| Add a new agent | Add node to `agents/graph.py`, update supervisor prompt, add tools to `agents/tools.py` |
| Change LLM provider at runtime | Set `LLM_PROVIDER` env var (openai/claude/ollama) |
| Debug a RAG query | Check `services/telemetry.py` store, or enable LangSmith with `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` |
| Add an off-topic category to block | Add regex to `_OUT_OF_SCOPE_PATTERNS` in `services/guardrails.py` |
| Add a PII type to redact | Add `(label, re.compile(...))` to `_PII_RULES` in `services/guardrails.py` |
| Add a prompt injection pattern | Add to `_INJECTION_PATTERNS` in `services/guardrails.py` |
| Add a new integration/data source | Add normalizer in `ingestion.py`, add `source_type` to Qdrant payload, update planner system prompt |
| Change alert routing | Update `AlertRoutingRule` rows (keywords/regex → team Slack/email/PagerDuty) |
| Suppress a recurring alert | Create `KnownIssue` row (by hash, regex, or Jira ticket key) |
