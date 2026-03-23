"""
OpsLens AI — Retention Policy Model
=======================================
Controls how long each data category is kept before automated cleanup.

One RetentionPolicy row per tenant. All fields are "days to retain" — rows
older than the threshold are deleted by the Celery retention task.
Set a field to 0 (or NULL) to retain indefinitely.

Categories managed:
    log_scan_history_days   — LogScanHistory rows (hourly digest results)
    timeline_event_days     — TimelineEvent rows (GitHub/Jira/log timeline)
    rrt_brief_days          — RRTBrief rows (incident artifacts)
    audit_log_days          — AuditLog rows (SOC2 — recommend ≥ 365)
    chat_session_days       — ChatSession + ChatMessage rows
    insight_days            — Insight rows

Qdrant embedding cleanup:
    embedding_days          — Vectors in Qdrant older than N days
                              (uses Qdrant scroll + delete by payload timestamp)

Archive before delete:
    archive_to_s3           — If True, export rows as JSONL to S3 before deletion
    s3_bucket               — S3 bucket name for archives
    s3_prefix               — Key prefix (e.g. "opslens-archive/{tenant_id}/")
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from apps.api.db.base import Base


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    __table_args__ = {"schema": "opslens"}

    id                      = Column(String, primary_key=True)
    tenant_id               = Column(String, nullable=False, unique=True, index=True)

    # Retention periods (days; 0 = retain forever)
    log_scan_history_days   = Column(Integer, nullable=False, default=90)
    timeline_event_days     = Column(Integer, nullable=False, default=180)
    rrt_brief_days          = Column(Integer, nullable=False, default=365)
    audit_log_days          = Column(Integer, nullable=False, default=730)   # 2 years
    chat_session_days       = Column(Integer, nullable=False, default=90)
    insight_days            = Column(Integer, nullable=False, default=90)
    embedding_days          = Column(Integer, nullable=False, default=180)

    # Archive settings (optional — requires S3 credentials in env)
    archive_to_s3           = Column(Boolean, nullable=False, default=False)
    s3_bucket               = Column(String, nullable=True)
    s3_prefix               = Column(String, nullable=True)

    # Stats (updated by retention task)
    last_run_at             = Column(DateTime(timezone=True), nullable=True)
    last_deleted_rows       = Column(Integer, nullable=True)
    last_run_notes          = Column(Text, nullable=True)

    is_active               = Column(Boolean, nullable=False, default=True)

    created_at              = Column(DateTime(timezone=True), nullable=False,
                                     default=lambda: datetime.now(tz=timezone.utc))
    updated_at              = Column(DateTime(timezone=True), nullable=False,
                                     default=lambda: datetime.now(tz=timezone.utc),
                                     onupdate=lambda: datetime.now(tz=timezone.utc))
