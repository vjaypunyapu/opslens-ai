# OpsLens AI — Architecture & How It Works

## Overview

OpsLens AI is a multi-tenant SaaS platform that monitors production logs, detects exceptions in real time, enriches alerts with historical context from Jira, GitHub, and Slack, and generates structured incident briefs for on-call engineers. It is built as a set of independently deployable services connected by a message queue, with a vector search layer for semantic retrieval.

---

## Deployment Topology

```
                          ┌─────────────────────────────────────────────────────┐
                          │                    Railway                           │
                          │                                                     │
  User browser  ──HTTPS──▶│  Next.js (web)  ──REST──▶  FastAPI (api)           │
                          │                                    │                │
                          │                              Celery tasks           │
                          │                                    ▼                │
                          │                        Redis (broker / results)     │
                          │                                    │                │
                          │                     ┌──────────────┴─────────────┐ │
                          │                     │   Celery Worker            │ │
                          │                     │   (worker service)         │ │
                          │                     └──────────────┬─────────────┘ │
                          │                                    │                │
                          │                     ┌──────────────┴─────────────┐ │
                          │                     │   Celery Beat              │ │
                          │                     │   (beat service — cron)    │ │
                          │                     └────────────────────────────┘ │
                          │                                                     │
                          │       PostgreSQL ◀────── all services ────────▶ Qdrant Cloud │
                          └─────────────────────────────────────────────────────┘
```

The system runs as four Railway services from a single monorepo and `railway.toml`. The start command dispatches to the correct process based on the `$RAILWAY_SERVICE_NAME` environment variable.

| Service | Process | Purpose |
|---------|---------|---------|
| `api` | `uvicorn apps.api.main:app` | REST API, auth, webhooks |
| `web` | `next build && next start` | React frontend |
| `worker` | `celery worker -Q celery,ingestion,insights,alerts,logs,rrt` | Async task execution |
| `beat` | `celery beat` | Cron scheduler — triggers periodic tasks |

---

## Data Stores

**PostgreSQL** is the system of record for all structured data. The main tables are:

- `canonical_documents` — normalised documents ingested from Jira, GitHub, Slack, Elasticsearch, Datadog, Splunk, CloudWatch, Azure Monitor, GCP Logging, and others. Each document has an `embedding_status` (pending → done) tracking whether it has been vectorised.
- `known_issues` — acknowledged/snoozed error patterns. When a log error matches a known issue, alerts are suppressed.
- `alert_routing_rules` — team routing configuration: which error patterns route to which Slack webhook / email / PagerDuty key, in what priority order.
- `rrt_briefs` — saved Rapid Response Team briefs with full lifecycle tracking (open → investigating → resolved).
- `timeline_events` — a chronological feed of engineering activity (PR merges, deploys, Jira transitions, log anomalies) used to answer "what changed right before this incident?".
- `incidents`, `alerts`, `insights` — higher-level incident management objects.

**Qdrant** is the vector store. Each tenant gets an isolated collection named `opslens_{tenant_id}`. Documents are parsed into structure-aware chunks (see Document Ingestion below), embedded with `text-embedding-3-small` (1536 dimensions), and upserted with a rich payload containing `source_type`, `title`, `url`, `author`, `heading`, `chunk_type`, `content_preview`, and `hyde_questions`.

**Redis** serves as both the Celery task broker and the result backend. It also backs rate limiting on the API.

---

## Core Pipelines

### 1 — Document Ingestion (background, continuous)

Raw records from each integration are normalised into a `RawRecord` struct by a source-specific `Normalizer` class (SlackNormalizer, JiraNormalizer, GitHubNormalizer, etc.), deduplicated by content hash, stored as `CanonicalDocument` rows in Postgres, and queued for embedding via `process_document.delay()`.

The worker task runs a three-stage pipeline:

```
External source            Worker                              Qdrant
(Jira / GitHub /  ──────▶  process_document task
 Slack / Datadog /          ├── fetch CanonicalDocument
 Elasticsearch /            ├── structural_parse(content)  ──▶  list[StructuralChunk]
 CloudWatch / etc.)         ├── generate_hyde_questions()  ──▶  augmented chunks
                            └── embed_and_upsert()         ──▶  opslens_{tenant_id}
```

#### Stage 1 — Structural Document Parser (`structural_parser.py`)

The ingestion pipeline no longer uses a naive 512-token sliding window over the raw text. Instead, `structural_parse()` runs a line-by-line state machine that understands document structure and applies four rules in priority order:

**Rule 1 — Fenced code blocks** (```` ``` ```` or `~~~`): The parser detects an opening fence, collects every line until the matching closing fence, and emits the block as a single atomic `StructuralChunk` with `chunk_type="code"`. The code language (e.g. `python`, `javascript`) is captured from the opening fence and stored in `metadata["code_language"]`. Code blocks are never split regardless of size.

**Rule 2 — Tables**: Both Markdown pipe tables (`|col|col|`) and HTML `<table>...</table>` blocks are detected and collected into a single atomic chunk with `chunk_type="table"`. A Markdown sequence must have at least a header row and a separator row to qualify; a lone `|` line falls through to prose. HTML tables are collected until `</table>` is found.

**Rule 3 — ATX headings** (`#`, `##`, `###`, etc.): When the parser encounters a heading, it flushes the current prose buffer and updates an internal heading stack. Every chunk produced after this point carries the heading breadcrumb (e.g. `"Authentication > OAuth Flow"`) prepended to its content and recorded in the `heading` field. This means the embedding for a paragraph buried under `## Authentication > ### OAuth Flow` encodes the full section path, not just the paragraph text, so a query about "OAuth token refresh" finds the right passage even if the passage itself never mentions "OAuth" by name.

**Rule 4 — Prose and list items**: Text lines that don't match any of the above are collected in a buffer and flushed when a structural boundary (heading, code fence, table) is encountered. The buffer is then split into 512-token chunks with 50-token overlap using tiktoken's `cl100k_base` encoder. Each chunk has the current heading breadcrumb prepended.

The output is a `list[StructuralChunk]`. Each `StructuralChunk` carries:

| Field | Type | Description |
|-------|------|-------------|
| `content` | str | Text sent to the embedding model — heading breadcrumb prepended |
| `raw_content` | str | Original text without heading prefix |
| `heading` | str | Breadcrumb, e.g. `"Authentication > OAuth Flow"` |
| `chunk_type` | str | `"text"` / `"code"` / `"table"` |
| `metadata` | dict | `code_language`, `heading_level`, `table_format`, etc. |
| `hyde_questions` | list[str] | Populated by Stage 2 |

#### Stage 2 — HyDE Augmentation (`generate_hyde_questions`)

After structural chunking, `generate_hyde_questions()` sends all chunks for a document to `gpt-4o-mini` in batches of 40. The model is asked to generate 1–3 hypothetical questions that only that specific chunk directly answers, mixing factual, procedural, reasoning, and diagnostic question types.

This technique is called Hypothetical Document Embeddings (HyDE). The intuition: users ask questions, documents contain answers. If the chunk's embedding also encodes question-shaped text, it will be closer in embedding space to a user's natural-language query.

The model responds with a single JSON object `{"results": [["q1","q2"], ["q1"], ...]}` — one sub-array per chunk. The questions are appended to the chunk's `content` (prefixed with `Q:`) before embedding, and stored separately in `hyde_questions` for inspection. HyDE is best-effort: if the API call fails, ingestion continues without questions — the structural chunks are still embedded and usable.

One batched API call per document (not one per chunk) keeps the cost well under $0.001 per document at current `gpt-4o-mini` pricing.

#### Stage 3 — Embed and Upsert

`embed_and_upsert()` receives the augmented `list[StructuralChunk]` and:

1. Creates the Qdrant collection on first use (cosine distance, 1536 dimensions).
2. Calls the OpenAI Embeddings API (`text-embedding-3-small`) in batches of 100, passing `chunk.content` — which now contains the heading prefix and any HyDE questions.
3. Upserts each point to Qdrant with a payload including `source_type`, `title`, `url`, `author`, `heading`, `chunk_type`, `hyde_questions`, `code_language` (for code chunks), and `content_preview` (taken from `raw_content[:400]` so the preview shown to users is clean, not augmented).

The task returns statistics: `chunks_total`, `code_chunks`, `table_chunks`, `hyde_chunks` (number of chunks that received questions).

#### Stage 4 — Dead-Letter Queue and Auto-Mitigation

Ingestion can fail at two independent layers, each with its own failure handling:

**Queue-level (`ingestion_queue` → `canonical_documents` normalisation).** Each raw record pulled from `opslens.ingestion_queue` is normalised, deduplicated, and upserted inside `_process_staging_batch`. If normalisation throws, the record is not lost — its `retry_count` is incremented and `next_retry_at` is set to `now() + 2^retry_count minutes` (capped at 8 hours). The next staging-batch run only selects rows where `next_retry_at IS NULL OR next_retry_at <= now()`, so failed records retry themselves on a backoff schedule with no manual intervention. Once `retry_count >= max_retries` (default 10), the record is moved to a permanent dead-letter state (`failed_at = now()`) and excluded from further automatic attempts.

**Document-level (embedding).** `process_document` is a Celery task with `max_retries=3` and Celery's own retry backoff. If all Celery retries are exhausted, `_mark_document_failed()` sets `CanonicalDocument.embedding_status = 'failed'`, records `processing_error`, and increments `processing_attempts`. The scheduled `retry_pending_embeddings` task (Celery Beat) re-dispatches `pending` documents every cycle, and also re-dispatches `failed` documents once their own backoff window has elapsed (`updated_at + 2^min(processing_attempts, 8) minutes <= now()`), up to `MAX_EMBEDDING_RETRY_ATTEMPTS` (8). Beyond that cap, a document is left for manual review.

**Manual override.** Three admin endpoints under `/api/v1/ingestion` exist for cases auto-mitigation can't resolve: `GET /dead-letter` (list permanently failed queue records), `POST /dead-letter/requeue` (reset a queue record's `failed_at`/`retry_count` for a fresh attempt), and `POST /dead-letter/requeue-embeddings` (reset `embedding_status` back to `pending` and re-dispatch). A `count_dead_letter` Beat task reports dead-letter counts on a schedule for alerting/monitoring.

---

### 2 — Fast Alert (every 5 minutes)

```
Celery Beat  ──▶  logs.fast_scan task
                  ├── _collect_lines()          pulls docker logs + file tails from last 5 min
                  ├── _extract_error_groups()   regex match → deduplicate by signature hash
                  ├── _is_known_issue_async()   DB check: suppressed? snoozed? Jira-tracked?
                  ├── _resolve_routing_async()  match AlertRoutingRules by priority order
                  ├── _write_timeline_event()   persist log_anomaly to TimelineEvent table
                  ├── build_fast_alert_payload() raw Slack Block Kit message (no LLM, < 2 min)
                  └── enrich_and_alert.delay()  queues enrichment task per team
```

**Tier 1 (immediate, no LLM):** The raw Slack alert fires within seconds of detection. The error group, count, and sample lines are formatted into Slack Block Kit and posted directly.

**Tier 2 (enriched, ~30 seconds later):** `enrich_and_alert` runs asynchronously:

```
enrich_and_alert task
├── _enrich_with_rag()    embed the error text → query Qdrant → top-5 related docs
├── _llm_diagnosis()      LLM prompt: "given this error + related context, what's likely wrong?"
├── build_enriched_alert_payload()  Slack message with AI diagnosis + Jira/Slack/GitHub links
└── generate_rrt_brief.delay()     queue full incident brief
```

---

### 3 — RAG Enrichment

When an error is detected, the exception text is embedded using the same model as ingestion (`text-embedding-3-small`) and searched against the tenant's Qdrant collection using `query_points()`, filtered to `source_type ∈ {jira, slack, github}`. The top-K results are returned with their title, URL, and content preview.

Because structural chunks carry their heading breadcrumb and HyDE questions inside the embedded content, the vector search is more semantically precise than before. A Jira ticket about the same payment timeout from two years ago will surface because its embedding was trained to answer questions like "what causes Stripe charge API to time out?" — which is exactly the shape of the incoming error query.

The embedding model is intentionally hardcoded to `text-embedding-3-small` in both `ingestion.py` and `log_fast_alert.py`. If the models diverge, query vectors would have different dimensions from stored vectors and Qdrant would return a 400 error.

**Filter-then-fallback:** If the source-type filtered query fails (e.g. during schema migrations or version mismatches), the system retries without filters and logs a warning, ensuring enrichment continues while the issue is diagnosed.

---

### 4 — RRT Brief Generation

```
generate_rrt_brief task
├── _fetch_timeline_context()    timeline events in the 60 min before the incident
│                                 (deploys, PR merges, Jira transitions → "what just changed?")
├── LLM prompt assembly          error details + related docs + timeline → structured brief
│   ├── WHAT HAPPENED
│   ├── IMPACT
│   ├── SUSPECTED CAUSE (LLM-generated)
│   ├── RELATED CONTEXT (Jira / GitHub / Slack from RAG)
│   ├── CHANGES BEFORE INCIDENT (timeline events — 60 min window)
│   └── NEXT ACTIONS (numbered, LLM-generated)
├── _save_rrt_brief()            persist to rrt_briefs table (open status)
└── Slack Block Kit delivery     formatted brief posted to each routing target
```

The brief is stored with a stable ID. Follow-up status updates (investigating → resolved) are posted as thread replies in the same Slack channel, keeping all incident context in one thread.

**Timeline vs RAG context:** The 60-minute timeline window answers "what just changed?" (deploys, PR merges) while RAG answers "what related issues exist anywhere in history?" (Jira tickets from any date). These are separate signals with different purposes in the brief.

---

### 5 — Multi-Agent Chat System (LangGraph)

The `/chat` page exposes a streaming conversational interface backed by a **LangGraph `StateGraph`** with a three-tier supervisor architecture. LangChain provides the LLM and retrieval primitives used inside each agent node; LangGraph controls the graph topology, state passing, and agent routing.

#### Graph topology

```
User query
    │
    ▼
[supervisor]   gpt-4o-mini classifies the intent and selects which specialist
    │          agents are needed (1–4 agents). Routing examples:
    │            "What's blocking the payment sprint?"  → research
    │            "Any active critical alerts?"          → alert
    │            "Show churn risk trends"               → insight
    │            "What happened in last night's outage?"→ incident + research
    │            "Summarise our ops health"             → research + insight + alert
    │
    ▼
[dispatcher]   runs all selected agents in parallel via asyncio.gather().
    │          Each agent appends its output to agent_outputs and its own
    │          trace entry to agent_trace (delta only — no duplication).
    │
    ▼
[synthesizer]  if only one agent ran → passes its answer through directly.
    │          if multiple agents ran → single LLM call merges all outputs
    │          into one coherent answer, then runs three validators in parallel:
    │            ├── Auditor     grounding: every claim traced to a source passage
    │            ├── Gatekeeper  completeness: finds unanswered sub-questions
    │            └── Strategist  coherence: catches circular reasoning / contradictions
    │
    ▼
  answer streamed to user (SSE)
```

#### Specialist agents

| Agent | Trigger phrases | Data sources |
|-------|----------------|--------------|
| **Research** | general Q&A, "what is", "how does", "why did" | Hybrid RAG (Qdrant dense + BM25 sparse + RRF merge + optional Cohere rerank) over `CanonicalDocument` |
| **Insight** | "trends", "patterns", "spikes", "churn", "bottleneck" | `insights` table + on-demand detector run (`run_insight_detector_now`) |
| **Alert** | "alerts", "critical", "firing", "severity" | `alerts` table via `get_active_alerts` / `get_alert_summary` |
| **Incident** | "outage", "incident", "timeline", "what happened", "RRT" | `rrt_briefs` table + deployment timeline via `get_incident_timeline` |

#### State and tracing

The graph passes a typed `AgentState` dict through every node. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `list[BaseMessage]` | Full conversation history (HumanMessage / AIMessage) |
| `next_agents` | `list[str]` | Set by supervisor — which agents the dispatcher will run |
| `agent_outputs` | `dict[str, str]` | Keyed by agent name, each agent's answer |
| `agent_trace` | `list[str]` | Ordered log of which agents completed, e.g. `["research:done", "alert:done"]` |
| `retrieved_docs` | `list[dict]` | Source passages returned by the Research agent for citation |
| `final_answer` | `str` | Populated by synthesizer, streamed to the client |

Each agent node returns only its own **delta** to `agent_trace` (e.g. `["research:done"]`). LangGraph's `add_messages` reducer merges deltas, preventing the N+1 duplication that would occur if each node returned the full accumulated trace.

#### Technology split

- **LangGraph** — `StateGraph`, node wiring, `START`/`END`, state reducer (`add_messages`)
- **LangChain** — `langchain_core.messages` (HumanMessage/AIMessage), `langchain-qdrant` retriever, LLM wrappers (`langchain-openai`, `langchain-anthropic`, `langchain-ollama`)

#### Hybrid retrieval (inside Research agent)

The Research agent wraps the existing `plan_and_answer` planner, which uses:

- **Dense search** — Qdrant vector search (`text-embedding-3-small`, 1536-dim) against the tenant's collection
- **BM25 sparse search** — keyword match over `CanonicalDocument.content` from Postgres; catches exact matches on error codes, ticket IDs, function names
- **RRF merge** — Reciprocal Rank Fusion (k=60) normalises and combines both rankings
- **Optional Cohere rerank** — if `COHERE_API_KEY` is set, a cross-encoder reranks the merged results

**Model routing** inside Research keeps costs proportional to query complexity: simple / single-source queries use `gpt-4o-mini`; complex / multi-source queries use `gpt-4o`. The planner's "simple"/"complex" classification drives this.

---

### 6 — Simulate Alert

The `POST /api/v1/log-ops/simulate` endpoint allows on-demand simulation of any error without waiting for the real log scanner. It:

1. Constructs an `ErrorGroup` from the provided error text
2. Runs the same routing rule resolution as the real scanner
3. Dispatches `enrich_and_alert.delay()` with the synthetic error group
4. Returns the Celery task ID for tracking

This is the primary way to demo the full pipeline end-to-end without needing a live service emitting errors.

---

## Alert Routing Engine

`AlertRoutingRule` rows define how errors are dispatched to teams. Each rule has:

- `service_patterns` — list of regex/glob strings matched against the service/container name
- `error_patterns` — list of regex/glob strings matched against the error line (any pattern hit = match when `match_all=False`)
- `source_containers` — optional list of container names to restrict the rule
- `slack_webhook` — team Slack incoming webhook URL
- `email_recipients` — list of email addresses
- `pagerduty_key` — PagerDuty Events API v2 routing key
- `priority` — integer (lower = higher priority). Rules are evaluated in ascending priority order.
- `stop_on_match` — if true, evaluation stops when this rule matches (no fan-out to lower-priority rules)
- `is_active` — toggle to disable a rule without deleting it

Rules are evaluated against each detected error group. Fan-out is supported: multiple rules can match and each team receives its own alert. The fallback is `LOG_FAST_ALERT_SLACK_WEBHOOK` if no rules match.

---

## Known Issue Suppression

Before firing an alert, `_is_known_issue_async()` checks the `KnownIssue` table for:

- **Signature match** — exact MD5 match of the normalised error line
- **Pattern match** — regex or keyword match against the error text
- **Snooze expiry** — `suppress_until` timestamp; if in the past, suppression is lifted automatically
- **Jira tracking** — if the issue has an associated Jira ticket key, it is treated as acknowledged

When a known issue match is found, `hit_count` and `last_hit_at` are updated for audit purposes. The alert is silently dropped.

---

## Asyncio / Celery Architecture

Celery uses `fork`-based concurrency (prefork pool). Each task runs in a worker process. Database access uses SQLAlchemy's async driver (`asyncpg`), which requires an active asyncio event loop.

**The problem:** a naive `asyncio.run()` call creates and immediately destroys an event loop. If asyncpg's connection pool was created at module import time (bound to a different loop), tasks fail with `RuntimeError: Future attached to a different loop`.

**The solution:** the worker's `apps/worker/db.py` creates its engine with `NullPool` — no connection is reused across calls. Each `async with AsyncSession()` opens a fresh connection inside the current event loop, uses it, and closes it. Additionally, all async calls in tasks go through a shared `run_async()` helper (`apps/worker/async_utils.py`) that creates a clean event loop, runs the coroutine, drains pending tasks, and closes cleanly.

---

## Multi-Tenant Isolation

Every database query is scoped to `tenant_id`. JWT tokens (RS256, issued by Clerk) carry the tenant identifier. The `AuthContext` dependency on every protected API route extracts and validates the tenant, ensuring one tenant cannot access another's data.

Qdrant uses per-tenant collections (`opslens_{tenant_id}`). All vector upserts and queries include the tenant ID both in the collection name and as a payload filter.

---

## LLM Provider Flexibility

The system supports three LLM providers selectable via `LLM_PROVIDER`:

| Provider | Chat model | Embed model | Use case |
|----------|-----------|-------------|---------|
| `openai` | GPT-4o | text-embedding-3-small | Default, best quality |
| `claude` | Claude Opus/Sonnet | text-embedding-3-small (OpenAI) | Enterprise, 200k context |
| `ollama` | Llama 3.1 (local) | nomic-embed-text (768-dim) | Fully private, no data egress |

Note: when switching embed models, the Qdrant collection must be recreated because stored vector dimensions are fixed at collection creation time.

---

## Notification Channels

| Channel | Implementation |
|---------|---------------|
| Slack | Incoming Webhook, Block Kit formatting. Per-team webhooks via routing rules. |
| Microsoft Teams | Office 365 Connector (MessageCard) or Power Automate Workflow (AdaptiveCard) |
| Email | SendGrid API, per-alert recipient lists from routing rules |
| PagerDuty | Events API v2, per-team routing keys, severity derived from error count |

---

## Frontend (Next.js)

The web application is a Next.js 14 app with Clerk authentication. It supports both dark and light themes via a `ThemeProvider` context that persists the preference to `localStorage` and sets a `data-theme` attribute on `<html>`. CSS custom properties (`--bg-app`, `--text-primary`, `--accent`, etc.) propagate the theme to every component without runtime JavaScript. The sidebar remains dark in both modes for visual contrast.

| Route | Purpose |
|-------|---------|
| `/incidents` | Live incident feed, Simulate Alert button, seed demo data |
| `/rrt-briefs` | View and update RRT briefs, status lifecycle |
| `/routing-rules` | CRUD for alert routing rules, dry-run test panel |
| `/alerts` | Alert history and acknowledgement |
| `/insights` | LLM-generated trend analysis (complaint spikes, feature trends, eng bottlenecks) |
| `/integrations` | Connect Jira, GitHub, Slack, observability sources |
| `/settings` | Retention policies, notification preferences, SAML SSO |
| `/admin` | RBAC, user management, audit log |

All API calls go through `apps/web/src/lib/api.ts` which attaches the Clerk session JWT and routes to the FastAPI backend.

---

## Key Design Decisions

**Structural parsing over naive chunking:** The original 512-token sliding window would split a code block mid-function or break a table row across two chunks, making those chunks semantically incoherent. The state-machine parser guarantees that code and table blocks are always atomic, and that prose chunks carry their section heading — solving the most common retrieval failure mode where a correct passage is missed because its embedding lacked context.

**HyDE at ingest time (not query time):** Classic HyDE generates hypothetical documents at query time, adding latency per query. OpsLens runs question generation once during ingestion and embeds the questions alongside the chunk. Query-time latency is zero. The tradeoff is slightly larger stored vectors and one extra `gpt-4o-mini` call per document at ingest, which is acceptable given that ingest is a background task.

**Two-tier alerting:** Tier 1 (raw, immediate, no LLM) ensures the on-call engineer is notified within 2 minutes of an exception spike, even if the LLM or Qdrant are slow. Tier 2 (enriched, async) adds context without blocking the critical notification path.

**Timeline vs RAG context:** The 60-minute timeline window answers "what just changed?" (deploys, PR merges) while RAG answers "what related issues exist anywhere in history?" (Jira tickets from any date). These are separate signals with different purposes in the brief.

**Qdrant filter-then-fallback:** If the source-type filter on the Qdrant query fails (e.g. during schema migrations or version mismatches), the system falls back to an unfiltered query and logs a warning. This ensures enrichment still works while the root cause is diagnosed.

**NullPool for Celery:** Connection pooling is incompatible with Celery's fork-based worker model and asyncpg's loop-bound connections. NullPool trades connection reuse for correctness — each task gets a fresh connection scoped to its own event loop.

**Validation before streaming:** The three-node validation graph (Auditor, Gatekeeper, Strategist) runs before any token reaches the user. If validation fails, the planner retries with targeted sub-queries derived from the Gatekeeper's `missing_queries` output. At most two retry loops run. After two failures, the partial answer is delivered with a ⚠️ disclaimer, ensuring the user always gets a response even in edge cases.

**LangGraph for orchestration, LangChain for primitives:** The multi-agent system uses LangGraph's `StateGraph` to define the supervisor→dispatcher→synthesizer topology and manage state passing between nodes. LangChain provides the LLM wrappers and retrieval components used inside each agent. This separation means the graph structure (routing, parallelism, state) is fully declarative in LangGraph, while the actual LLM calls and retrieval strategies remain swappable LangChain primitives.

**Parallel dispatch with delta-only trace:** The dispatcher runs all selected specialist agents concurrently via `asyncio.gather()`. To avoid N+1 duplication in `agent_trace`, each node returns only its own trace delta (e.g. `["research:done"]`); LangGraph's reducer merges deltas into the accumulated list. This means trace entries are always unique regardless of how many agents ran or in what order they completed.

**Single synthesizer call for multi-agent answers:** When multiple agents run, a single LLM call receives all agent outputs and merges them into one coherent answer rather than naively concatenating responses. When only one agent runs, the synthesizer is a pass-through with no additional LLM cost.

**Self-healing dead-letter queue over manual requeue:** Transient failures (rate limits, brief Qdrant/OpenAI outages, momentary DB contention) are far more common than permanent ones, so both ingestion layers retry automatically on an exponential backoff before anything is surfaced to an operator. Manual requeue endpoints still exist as a fallback for genuinely broken records (malformed payloads, schema mismatches), but they are the exception path, not the primary recovery mechanism.
