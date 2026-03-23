"""SQLAlchemy ORM models — mirrors apps/api/db/schema.sql exactly."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.db.session import Base


# ─── Tenants ──────────────────────────────────────────────────────────────────
class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="starter")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    users: Mapped[list[User]] = relationship("User", back_populates="tenant")
    integrations: Mapped[list[Integration]] = relationship("Integration", back_populates="tenant")
    documents: Mapped[list[CanonicalDocument]] = relationship(
        "CanonicalDocument", back_populates="tenant"
    )
    insights: Mapped[list[Insight]] = relationship("Insight", back_populates="tenant")
    alert_rules: Mapped[list[AlertRule]] = relationship("AlertRule", back_populates="tenant")
    chat_sessions: Mapped[list[ChatSession]] = relationship("ChatSession", back_populates="tenant")


# ─── Users ────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)  # Clerk/Auth0 user ID
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    chat_sessions: Mapped[list[ChatSession]] = relationship("ChatSession", back_populates="user")


# ─── Integrations ────────────────────────────────────────────────────────────
class Integration(Base):
    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_type", name="uq_integrations_tenant_source"),
        {"schema": "opslens"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    airbyte_connection_id: Mapped[str | None] = mapped_column(String(255))
    airbyte_source_id: Mapped[str | None] = mapped_column(String(255))
    credentials: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # AES-256 encrypted
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)       # Source-specific config
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_records: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="integrations")


# ─── Canonical Documents ──────────────────────────────────────────────────────
class CanonicalDocument(Base):
    __tablename__ = "canonical_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "content_hash", name="uq_canonical_documents_hash"),
        {"schema": "opslens"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    embedding_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="documents")


# ─── Insights ─────────────────────────────────────────────────────────────────
class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False
    )
    insight_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    magnitude: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    source_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="insights")


# ─── Alert Rules ──────────────────────────────────────────────────────────────
class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    channels: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="alert_rules")
    history: Mapped[list[AlertHistory]] = relationship("AlertHistory", back_populates="rule")


# ─── Alert History ────────────────────────────────────────────────────────────
class AlertHistory(Base):
    __tablename__ = "alert_history"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    channels_notified: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    rule: Mapped[AlertRule] = relationship("AlertRule", back_populates="history")


# ─── Chat Sessions ────────────────────────────────────────────────────────────
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.users.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="chat_sessions")
    user: Mapped[User | None] = relationship("User", back_populates="chat_sessions")
    messages: Mapped[list[ChatMessage]] = relationship(
        "ChatMessage", back_populates="session", order_by="ChatMessage.created_at"
    )


# ─── Chat Messages ────────────────────────────────────────────────────────────
class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opslens.chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    token_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[int | None] = mapped_column(SmallInteger)  # 1 = thumbs_up, -1 = thumbs_down
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ChatSession] = relationship("ChatSession", back_populates="messages")


# ─── Incidents ────────────────────────────────────────────────────────────────
class Incident(Base):
    """
    Represents a production incident investigation.
    Stores the correlated timeline and AI-generated root cause analysis.
    """
    __tablename__ = "incidents"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Status: open | investigating | analysing | resolved | closed
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="p2")  # p0/p1/p2/p3/p4
    service: Mapped[str | None] = mapped_column(String(255))   # affected service name
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # AI-generated content (populated by the investigation worker)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    root_cause: Mapped[str | None] = mapped_column(Text)
    contributing_factors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    recommendations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    created_by: Mapped[str | None] = mapped_column(String(255))  # Clerk user_id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship("Tenant")


# ─── Ingestion Queue ──────────────────────────────────────────────────────────
class IngestionQueue(Base):
    """
    Staging table for raw records received from Airbyte webhooks or direct API
    calls before they are normalised and embedded.
    """
    __tablename__ = "ingestion_queue"
    __table_args__ = {"schema": "opslens"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opslens.tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
