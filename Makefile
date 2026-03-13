# ─── OpsLens AI — Project Makefile ────────────────────────────────────────────
.PHONY: help install dev test lint format build up down logs db-init db-migrate \
        db-upgrade qdrant-init clean

PYTHON   := python3
PIP      := pip3
DC       := docker compose -f infra/docker/docker-compose.yml
DC_ENV   := --env-file .env

help:          ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Local Dev ────────────────────────────────────────────────────────────────
install:       ## Install Python dependencies into a venv
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	@echo "✅  Run: source .venv/bin/activate"

dev:           ## Start FastAPI dev server with hot-reload
	uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

worker:        ## Start Celery worker (all queues)
	celery -A apps.worker.celery_app worker --loglevel=debug \
	  -Q ingestion,insights,alerts --concurrency=2

beat:          ## Start Celery Beat scheduler
	celery -A apps.worker.celery_app beat --loglevel=info

flower:        ## Open Celery Flower monitor
	celery -A apps.worker.celery_app flower --port=5555

# ─── Testing ──────────────────────────────────────────────────────────────────
test:          ## Run test suite with coverage
	pytest tests/ -v --tb=short --cov=apps --cov-report=term-missing

test-ci:       ## Run tests in CI mode (no coverage html)
	pytest tests/ -v --tb=short --cov=apps --cov-report=xml

# ─── Linting / Formatting ─────────────────────────────────────────────────────
lint:          ## Run ruff linter
	ruff check apps/ tests/

format:        ## Auto-format with black + ruff
	black apps/ tests/
	ruff check --fix apps/ tests/

typecheck:     ## Run mypy type checker
	mypy apps/ --ignore-missing-imports

# ─── Docker ───────────────────────────────────────────────────────────────────
build:         ## Build Docker images
	docker build -f Dockerfile.api    -t opslens-api:latest .
	docker build -f Dockerfile.worker -t opslens-worker:latest .

up:            ## Start all services via Docker Compose
	$(DC) $(DC_ENV) up -d

down:          ## Stop all services
	$(DC) down

logs:          ## Tail logs for all services
	$(DC) logs -f

ps:            ## Show running containers
	$(DC) ps

# ─── Database ─────────────────────────────────────────────────────────────────
db-init:       ## Apply raw SQL schema (first-time setup)
	psql $$DATABASE_URL -f apps/api/db/schema.sql

db-revision:   ## Create a new Alembic migration (MSG="description")
	alembic revision --autogenerate -m "$(MSG)"

db-upgrade:    ## Apply pending Alembic migrations
	alembic upgrade head

db-downgrade:  ## Rollback one migration
	alembic downgrade -1

db-history:    ## Show migration history
	alembic history --verbose

# ─── Qdrant ───────────────────────────────────────────────────────────────────
qdrant-init:   ## Initialise Qdrant collections for all tenants
	$(PYTHON) scripts/init_qdrant_collections.py --all-tenants

# ─── Utilities ────────────────────────────────────────────────────────────────
clean:         ## Remove build artefacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache htmlcov .coverage coverage.xml dist build *.egg-info
