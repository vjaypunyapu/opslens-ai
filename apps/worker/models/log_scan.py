"""
OpsLens AI – LogScanHistory model
===================================
Stores the result of each log scan run so history can be shown in the UI
and queried via the alerts router.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from apps.api.db.base import Base


class LogScanHistory(Base):
    __tablename__ = "log_scan_history"
    __table_args__ = {"schema": "opslens"}

    id             = Column(String, primary_key=True)
    tenant_id      = Column(String, nullable=False, index=True)
    scanned_at     = Column(DateTime(timezone=True), nullable=False,
                            default=lambda: datetime.now(tz=timezone.utc))
    window_minutes = Column(Integer, nullable=False)
    issue_count    = Column(Integer, nullable=False, default=0)
    summary        = Column(Text, nullable=True)       # LLM digest
    channels_sent  = Column(ARRAY(String), nullable=False, default=list)
    raw_issues     = Column(JSONB, nullable=True)      # optional: top issue groups
