# OpsLens AI — Operational Intelligence Copilot

> Ask questions about your business operations in plain English. OpsLens AI ingests data from Slack, Jira, Google Drive, Zendesk, GitHub, and HubSpot, then surfaces proactive insights using RAG + LLM-powered analysis.

[![CI](https://github.com/your-org/opslens-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/opslens-ai/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/your-org/opslens-ai/branch/main/graph/badge.svg)](https://codecov.io/gh/your-org/opslens-ai)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com/)

---
## Architecture
```
┌────────────────────────────────────────────────────────────────────────┐
│  Layer 1 — Data Understanding                                          │
│  Airbyte → Staging DB → Normalize → Deduplicate → Chunk → Embed       │
│  (Slack / Jira / Google Drive / Zendesk / GitHub / Bitbucket / HubSpot)           │
└─────────────────────────────┬──────────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────────┐
│  Layer 2 — Intelligence Engine                                         │
│  RAG Service (LangChain + Qdrant)  │  Insight Engine (5 detectors)    │
│  Alert Runner (rules + dispatch)   │  Celery Beat (scheduled tasks)    │
└─────────────────────────────┬──────────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────────┐
│  Layer 3 — Business UX                                                 │
│  Chat (SSE streaming)  │  Dashboard  │  Alerts  │  Insights Feed       │
└────────────────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI 0.111, Python 3.11, Uvicorn |
| Auth | Clerk / Auth0 — JWT RS256, RBAC |
| Database | PostgreSQL 15 (SQLAlchemy async + Alembic) |
| Vector Store | Qdrant (per-tenant collections, cosine similarity) |
| LLM / RAG | LangChain LCEL, GPT-4o, text-embedding-3-small |
| Task Queue | Celery + Redis (Beat scheduler) |
| ETL | Airbyte (Slack, Jira, GDrive, Zendesk, GitHub, HubSpot) |
| Infra | Docker Compose (dev) · AWS ECS Fargate + RDS + ElastiCache (prod) |
| CI/CD | GitHub Actions → ECR → ECS |

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Docker & Docker Compose
- An OpenAI API key

### 2. Clone & Configure

```bash
git clone https://github.com/your-org/opslens-ai.git
cd opslens-ai

cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY, SECRET_KEY, and JWT_PUBLIC_KEY_URL at minimum
```

### 3. Start Local Services

```bash
# Spin up Postgres, Redis, Qdrant, and Airbyte
make up

# Wait ~30s, then apply the database schema
make db-init

# Initialise Qdrant collections for the demo tenant
make qdrant-init
```

### 4. Run the API

```bash
# Install Python dependencies
make install
source .venv/bin/activate

# Start the FastAPI dev server (hot-reload)
make dev
# → http://localhost:8000/docs
```

### 5. Run the Worker

Open a second terminal:

```bash
source .venv/bin/activate
make worker
```

---

## Project Structure

```
opslens-ai/
├── apps/
│   ├── api/
│   │   ├── auth/           # JWT middleware + RBAC dependencies
│   │   ├── db/             # SQLAlchemy session, schema.sql
│   │   ├── routers/        # rag, ingestion, insights, alerts
│   │   ├── services/       # RagService (LangChain LCEL)
│   │   ├── config.py       # Pydantic-settings
│   │   └── main.py         # FastAPI app factory
│   └── worker/
│       ├── tasks/
│       │   ├── ingestion.py        # 5-stage doc processing pipeline
│       │   ├── insight_engine.py   # 5 AI detectors (BaseDetector ABC)
│       │   ├── insight_runner.py   # LLM-based pattern analysis
│       │   └── alert_runner.py     # Rule evaluator + Slack/email dispatch
│       └── celery_app.py   # Celery + Beat config
├── migrations/             # Alembic (env.py + versions/)
├── infra/
│   └── docker/
│       └── docker-compose.yml
├── scripts/
│   └── init_qdrant_collections.py
├── tests/
│   └── test_insight_engine.py
├── .github/
│   └── workflows/
│       ├── ci.yml          # Lint → Test → Docker build
│       └── deploy.yml      # Build → Push ECR → Migrate → Deploy ECS
├── .env.example
├── .gitignore
├── alembic.ini
├── Dockerfile.api
├── Dockerfile.worker
├── Makefile
├── pyproject.toml
└── requirements.txt
```

---

## API Overview

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rag/sessions` | Create a new chat session |
| `POST` | `/api/v1/rag/sessions/{id}/query` | SSE streaming query |
| `GET` | `/api/v1/rag/sessions` | List sessions |
| `POST` | `/api/v1/ingestion/integrations` | Connect a source (Slack, Jira…) |
| `POST` | `/api/v1/ingestion/webhook/airbyte` | Airbyte sync-complete webhook |
| `GET` | `/api/v1/insights` | List detected insights |
| `POST` | `/api/v1/insights/generate` | Trigger on-demand insight run |
| `GET` | `/api/v1/alerts/rules` | List alert rules |
| `POST` | `/api/v1/alerts/rules` | Create alert rule |
| `POST` | `/api/v1/alerts/rules/{id}/test` | Test-fire an alert |

Full interactive docs: `http://localhost:8000/docs`

---

## Insight Detectors

| Detector | Trigger | Schedule |
|---|---|---|
| `ComplaintSpikeDetector` | Zendesk/Slack complaint keywords spike >2σ | Every hour |
| `FeatureTrendDetector` | Jira/Slack feature request clustering | Every 6 hours |
| `ReleaseCorrelationDetector` | GitHub release ↔ support ticket spike ±48h | Every 4 hours |
| `EngBottleneckDetector` | Jira tickets stale >5 days | Every 12 hours |
| `ChurnRiskDetector` | ≥3 urgent open tickets per org | Every 2 hours |

---

## Deployment (AWS)

```bash
# Build and push to ECR, run migrations, deploy to ECS
# Triggered automatically on push to main, or manually:
gh workflow run deploy.yml -f environment=production
```

Required GitHub Secrets: `AWS_ACCOUNT_ID`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `ECS_SUBNET_IDS`, `ECS_SECURITY_GROUP_ID`, `SLACK_WEBHOOK_URL`, `CODECOV_TOKEN`.

---

## Development Commands

```bash
make help         # Show all available commands
make test         # Run test suite with coverage
make lint         # Ruff lint check
make format       # Auto-format with black + ruff
make db-revision MSG="add users table"  # New Alembic migration
make db-upgrade   # Apply pending migrations
make flower       # Celery task monitor (localhost:5555)
```

---

## Contributing

1. Branch from `develop` — `git checkout -b feat/my-feature`
2. Write tests for new functionality
3. Run `make lint test` before pushing
4. Open a PR against `develop`; `main` is production-only

---

## License

Proprietary — All rights reserved © OpsLens AI
