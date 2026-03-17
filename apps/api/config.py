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
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]
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
    # Set LLM_PROVIDER=ollama to keep all data on-premises (no OpenAI calls).
    LLM_PROVIDER: Literal["openai", "ollama"] = "openai"

    # ── OpenAI ────────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    OPENAI_CHAT_MODEL: str = "gpt-4o"
    OPENAI_MAX_TOKENS: int = 2048
    OPENAI_TEMPERATURE: float = 0.0

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
    AIRBYTE_API_URL: str = "http://localhost:8000/api/v1"
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
    ALERT_FROM_EMAIL: str = "alerts@opslens.ai"

    # ── LangSmith (optional) ──────────────────────────────────────────────────
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str | None = None
    LANGCHAIN_PROJECT: str = "opslens"

    # ── Sentry (optional) ─────────────────────────────────────────────────────
    SENTRY_DSN: str | None = None

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.ENV == "production":
            assert self.OPENAI_API_KEY, "OPENAI_API_KEY must be set in production"
            assert self.JWT_PUBLIC_KEY, "JWT_PUBLIC_KEY must be set in production"
            assert "CHANGE_ME" not in self.SECRET_KEY, "Replace SECRET_KEY in production"
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Module-level singleton for convenience
settings: Settings = get_settings()
