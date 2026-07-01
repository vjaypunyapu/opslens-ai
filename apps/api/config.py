"""
OpsLens AI — Application Configuration
========================================
All settings are read from environment variables (via .env in dev).
Use pydantic-settings so every setting is type-validated at startup.

Required in production:
    OPENAI_API_KEY, JWT_PUBLIC_KEY, DATABASE_URL, QDRANT_URL

Optional but recommended:
    LANGCHAIN_API_KEY  (LangSmith tracing)
    SENDGRID_API_KEY   (email alerts)
    SENTRY_DSN         (error tracking)
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ───────────────────────────────────────────────────────────────────
    ENV: Literal["development", "staging", "production"] = "development"
    APP_NAME: str = "OpsLens AI"
    SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION_USE_32+_CHARS"
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    LOG_LEVEL: str = "INFO"

    # ── Database (PostgreSQL) ─────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://opslens:opslens_dev@localhost:5432/opslens"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    # ── Redis / Celery ─────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_MAX_REQUESTS: int = 100
    RATE_LIMIT_CHAT_MAX: int = 10   # stricter for streaming chat

    # ── Qdrant ────────────────────────────────────────────────────────────────
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str | None = None
    QDRANT_EMBED_DIMS: int = 1536
    QDRANT_TOP_K: int = 10
    QDRANT_COLLECTION_PREFIX: str = "opslens_"

    # ── LLM Provider ─────────────────────────────────────────────────────────
    # "openai"     → GPT-4o + text-embedding-3-small (best quality, data leaves org)
    # "ollama"     → Llama 3.1 + nomic-embed-text (fully private, on-prem, no API cost)
    # "claude"     → Claude (Anthropic) + OpenAI embeddings (enterprise BAA, 200k context)
    LLM_PROVIDER: Literal["openai", "ollama", "claude"] = "openai"

    # ── OpenAI ────────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    OPENAI_CHAT_MODEL: str = "gpt-4o"
    OPENAI_MAX_TOKENS: int = 2048
    OPENAI_TEMPERATURE: float = 0.0

    # ── Anthropic / Claude ────────────────────────────────────────────────────
    # Used when LLM_PROVIDER=claude.
    # Embeddings still use OpenAI (Anthropic has no embeddings API).
    # Get your key at: https://console.anthropic.com/
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_CHAT_MODEL: str = "claude-opus-4-6"   # or claude-sonnet-4-6 for lower cost

    # ── Ollama (on-prem / private LLM) ────────────────────────────────────────
    OLLAMA_URL: str = "http://ollama:11434"
    OLLAMA_CHAT_MODEL: str = "llama3.1"
    OLLAMA_EMBED_MODEL: str = "nomic-embed-text"  # 768-dim, fast, local

    # ── Auth (Clerk / Auth0 — RS256 JWT) ──────────────────────────────────────
    JWT_ALGORITHM: str = "RS256"
    JWT_PUBLIC_KEY: str = ""         # RSA PEM (optional if URL set)
    JWT_PUBLIC_KEY_URL: str | None = None  # JWKS endpoint (Clerk/.well-known/jwks.json)
    JWT_AUDIENCE: str | None = None
    JWT_ISSUER: str | None = None

    # ── Airbyte ───────────────────────────────────────────────────────────────
    AIRBYTE_API_URL: str = "http://localhost:8006/api/v1"
    AIRBYTE_USERNAME: str = "airbyte"
    AIRBYTE_PASSWORD: str = "password"


    # ── Airbyte source definition IDs (override per deployment) ──────────────
    AIRBYTE_SOURCE_DEF_ELASTICSEARCH: str = ""
    AIRBYTE_SOURCE_DEF_DATADOG: str = ""
    AIRBYTE_SOURCE_DEF_CLOUDWATCH: str = ""
    AIRBYTE_SOURCE_DEF_SPLUNK: str = ""
    AIRBYTE_SOURCE_DEF_AZURE_MONITOR: str = ""
    AIRBYTE_SOURCE_DEF_GCP_LOGGING: str = ""

    # ── Chunking ──────────────────────────────────────────────────────────────
    CHUNK_TOKENS: int = 512
    CHUNK_OVERLAP_TOKENS: int = 50
    EMBED_BATCH_SIZE: int = 100

    # ── Notifications ─────────────────────────────────────────────────────────
    SENDGRID_API_KEY: str | None = None
    ALERT_FROM_EMAIL: str = "alerts@opslensai.com"

    # ── Transactional Email (Resend) ──────────────────────────────────────────
    # Sign up at resend.com, verify your domain, then add your API key.
    # Leave empty in dev to skip sending (emails are logged instead).
    RESEND_API_KEY: str = ""
    INVITE_FROM_EMAIL: str = "hello@opslensai.com"
    APP_URL: str = "https://app.opslensai.com"   # used to build invite links

    # ── Platform Admin (Founder Panel) ───────────────────────────────────────
    # Comma-separated list of email addresses that can access /api/v1/platform/*
    # These users can list all tenants and generate first-admin invite links for
    # any workspace without being a member of that workspace.
    # Example: "vj@opslensai.com,cofounder@opslensai.com"
    PLATFORM_ADMIN_EMAILS: str = ""

    # ── Log Scanner (hourly LLM digest) ──────────────────────────────────────
    # Runs once per hour, uses LLM to summarise all issues, sends to team.
    LOG_SCAN_ENABLED: bool = True
    LOG_SCAN_WINDOW_MINUTES: int = 60          # how far back to look each run
    LOG_SCAN_MAX_LINES: int = 2000             # max lines to pull per source
    LOG_SCAN_CRON_MINUTES: int = 60            # run every N minutes (Beat schedule)
    LOG_SCAN_MIN_ISSUES: int = 1               # suppress report if fewer issues found
    LOG_SCAN_DOCKER_CONTAINERS: str = "opslens-api,opslens-worker"  # comma-separated
    LOG_SCAN_FILE_PATH: str | None = None      # optional: /var/log/opslens/app.log
    LOG_SCAN_SLACK_WEBHOOK: str | None = None  # webhook for hourly digest
    LOG_SCAN_EMAIL_RECIPIENTS: str | None = None  # comma-separated email list

    # ── Fast Alert + Exception Enricher ──────────────────────────────────────
    # Runs every 5 min. On new exception: fires Slack immediately (no LLM),
    # then queries Qdrant (Jira/Slack/GitHub) for related context + LLM diagnosis.
    LOG_FAST_ALERT_ENABLED: bool = True
    LOG_FAST_ALERT_CRON_MINUTES: int = 5       # how often to run the fast scan
    LOG_FAST_ALERT_WINDOW_MINUTES: int = 5     # look-back window per run
    LOG_FAST_ALERT_THRESHOLD: int = 3          # min error occurrences to fire
    LOG_FAST_ALERT_COOLDOWN_MINUTES: int = 10  # in-process dedup (fast scan only)
    LOG_INCIDENT_COOLDOWN_HOURS: float = 0.25  # DB-level RRT brief dedup (15 min)
    LOG_FAST_ALERT_ENRICH: bool = True         # query Jira/Slack/GitHub for context
    LOG_FAST_ALERT_ENRICH_TOP_K: int = 5       # max related docs to surface
    LOG_FAST_ALERT_SLACK_WEBHOOK: str | None = None  # webhook (falls back to LOG_SCAN_SLACK_WEBHOOK)

    # ── Delivery Risk Timeline ────────────────────────────────────────────────
    # GitHub webhook integration
    GITHUB_WEBHOOK_SECRET: str = ""        # GitHub webhook secret (from repo/org settings)
    GITHUB_ORG: str = ""                   # Restrict to a specific GitHub org (optional)
    # Sync depth — increase these if you want more historical data per sync.
    # Higher values mean longer sync times and more API calls (5000 req/hr limit).
    GITHUB_SYNC_MAX_REPOS: int = 50        # repos to sync (sorted by most recently updated)
    GITHUB_SYNC_MAX_ISSUES: int = 200      # issues+PRs per repo (paginated, 100/page)
    GITHUB_SYNC_MAX_COMMITS: int = 100     # commits per repo

    # ── Contextual source re-sync schedule ───────────────────────────────────
    # GitHub, Jira, Slack, HubSpot, Zendesk, Google Drive are re-synced on a
    # daily cron so new content is picked up automatically without manual "Sync now".
    # Set CONTEXTUAL_SYNC_CRON_HOUR=* to sync every hour, or adjust to taste.
    # Times are UTC. Defaults to 03:00 UTC daily (off-peak for most timezones).
    CONTEXTUAL_SYNC_CRON_HOUR: str = "3"    # hour(s), crontab syntax e.g. "3" or "*/6"
    CONTEXTUAL_SYNC_CRON_MINUTE: str = "0"  # minute(s), e.g. "0" or "30"

    # Jira webhook integration
    JIRA_WEBHOOK_TOKEN: str = ""           # Secret token set in Jira webhook config
    JIRA_HOST: str = ""                    # e.g. "yourcompany.atlassian.net"

    # Multi-tenant: default tenant for single-tenant installs or dev mode
    DEFAULT_TENANT_ID: str = "default"

    # ── Notification Provider ─────────────────────────────────────────────────
    # "slack"  → Slack Block Kit (default)
    # "teams"  → Microsoft Teams (MessageCard or AdaptiveCard)
    NOTIFICATION_PROVIDER: Literal["slack", "teams"] = "slack"

    # Teams webhook format (only used when NOTIFICATION_PROVIDER=teams):
    # "connector" → Office 365 Incoming Webhook / MessageCard (most common, legacy)
    # "workflow"  → Power Automate Workflow / AdaptiveCard (modern)
    TEAMS_WEBHOOK_TYPE: Literal["connector", "workflow"] = "connector"

    # ── PagerDuty ─────────────────────────────────────────────────────────────
    # Global fallback key — per-team keys are stored on AlertRoutingRule.pagerduty_key
    PAGERDUTY_ROUTING_KEY: str = ""

    # ── Enterprise SSO (SAML / OIDC / LDAP) ──────────────────────────────────
    # Per-tenant SAML config is stored in DB; these are SP-level defaults.
    SAML_SP_ENTITY_ID: str = "https://app.opslens.ai"
    SAML_SP_BASE_URL: str = "https://app.opslens.ai"   # used to build ACS / SLO URLs

    # Frontend URL used for SSO callbacks.
    # After SAML/OIDC login, the user is redirected to:
    #   {FRONTEND_URL}/auth/sso-callback?token=<jwt>
    # The frontend should extract the token and use it as a Bearer header.
    FRONTEND_URL: str = "http://localhost:3000"

    # Lifetime (seconds) for internally-issued SSO tokens (SAML/OIDC/LDAP).
    # Default: 3600 (1 hour). Rotate SECRET_KEY to invalidate all sessions.
    INTERNAL_TOKEN_TTL_SECONDS: int = 3600

    # ── Storage Optimisation ──────────────────────────────────────────────────
    # Minimum log level to ingest into canonical_documents for log-type sources
    # (elasticsearch, datadog, cloudwatch, gcp, splunk, azuremonitor).
    # Contextual sources (slack, jira, github, gdrive, zendesk, hubspot) are
    # always fully ingested regardless of this setting.
    # Valid values (case-insensitive): DEBUG | INFO | WARNING | ERROR | CRITICAL
    # Default: WARNING — drops DEBUG/INFO rows which are the bulk of log volume.
    INGEST_LOG_MIN_LEVEL: str = "WARNING"

    # Maximum characters stored in canonical_documents.content for log-type sources.
    # Stack traces beyond this length are truncated (a suffix note is appended).
    # Set to 0 to disable truncation.
    # Default: 8000 — covers even long Java stack traces without wasting space.
    INGEST_LOG_CONTENT_MAX_CHARS: int = 8000

    # How many days to keep PROCESSED rows in opslens.ingestion_queue.
    # Rows with processed_at IS NOT NULL older than this are deleted by the
    # daily retention task. Set to 0 to retain forever.
    # Default: 7 days — processed rows have no ongoing value.
    STAGING_QUEUE_RETENTION_DAYS: int = 7

    # ── S3 / S3-compatible object storage (optional) ────────────────────────────
    # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION are shared
    # by RetentionPolicy archival (archive_to_s3=true) and general asset storage
    # below. AWS_ENDPOINT_URL overrides the default AWS endpoint for
    # S3-compatible providers (e.g. Railway Bucket / Tigris) — leave unset for
    # real AWS S3.
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_DEFAULT_REGION: str = "us-east-1"
    AWS_ENDPOINT_URL: str | None = None
    RETENTION_ARCHIVE_BUCKET: str | None = None
    # General-purpose bucket for app assets (tenant logos, etc.) — separate from
    # the retention archive bucket since they have different lifecycle needs.
    AWS_S3_BUCKET_NAME: str | None = None

    # ── LangSmith (optional) ──────────────────────────────────────────────────
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str | None = None
    LANGCHAIN_PROJECT: str = "opslens"

    # ── Sentry (optional) ─────────────────────────────────────────────────────
    SENTRY_DSN: str | None = None

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        # Railway injects postgres:// or postgresql:// — SQLAlchemy asyncpg needs postgresql+asyncpg://
        if isinstance(v, str):
            if v.startswith("postgres://"):
                v = v.replace("postgres://", "postgresql+asyncpg://", 1)
            elif v.startswith("postgresql://"):
                v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.ENV == "production":
            if self.LLM_PROVIDER == "claude":
                assert self.ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY must be set when LLM_PROVIDER=claude"
                # Claude still uses OpenAI for embeddings
                assert self.OPENAI_API_KEY, "OPENAI_API_KEY must be set for embeddings when LLM_PROVIDER=claude"
            elif self.LLM_PROVIDER == "openai":
                assert self.OPENAI_API_KEY, "OPENAI_API_KEY must be set in production"
            # ollama needs no API keys
            assert self.JWT_PUBLIC_KEY, "JWT_PUBLIC_KEY must be set in production"
            assert "CHANGE_ME" not in self.SECRET_KEY, "Replace SECRET_KEY in production"
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Module-level singleton for convenience
settings: Settings = get_settings()
