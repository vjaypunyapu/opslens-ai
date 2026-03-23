"""
OpsLens AI — RRT Brief Model
==============================
An RRT (Rapid Response Team) Brief is a structured, reusable incident artifact
generated automatically when a critical exception is detected and enriched.

Lifecycle:  open → investigating → resolved
            (can be updated via API or Slack slash command)

Each brief:
  - Has a unique ID and stable URL
  - Is delivered to Slack/Teams with full formatting
  - Tracks the Slack thread timestamp so follow-up updates reply in-thread
  - Stores all structured fields so the manager dashboard can display them
  - Is linked back to the triggering error signature for dedup
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from apps.api.db.base import Base


class RRTBrief(Base):
    __tablename__ = "rrt_briefs"
    __table_args__ = {"schema": "opslens"}

    id               = Column(String, primary_key=True)
    tenant_id        = Column(String, nullable=False, index=True)

    # ── Core incident fields ──────────────────────────────────────────────────
    title            = Column(String, nullable=False)          # short headline
    what_happened    = Column(Text, nullable=False)            # plain description
    impact           = Column(Text, nullable=True)             # affected services/features
    started_at       = Column(DateTime(timezone=True), nullable=True)   # estimated start
    detected_at      = Column(DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(tz=timezone.utc))
    suspected_cause  = Column(Text, nullable=True)             # LLM-generated root cause
    next_actions     = Column(ARRAY(String), nullable=False, default=list)

    # ── Related evidence ──────────────────────────────────────────────────────
    # Each item: {type: jira|github|slack, title, url, snippet, score}
    related_items    = Column(JSONB, nullable=False, default=list)

    # ── Ownership ─────────────────────────────────────────────────────────────
    owner_team       = Column(String, nullable=True)           # from routing rule
    owner_contacts   = Column(ARRAY(String), nullable=False, default=list)  # emails

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    status           = Column(String, nullable=False, default="open")
    # "open" | "investigating" | "resolved"
    resolved_at      = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)

    # ── Traceability ──────────────────────────────────────────────────────────
    error_signature  = Column(String, nullable=True, index=True)
    error_sample     = Column(Text, nullable=True)             # first few log lines

    # ── Delivery ──────────────────────────────────────────────────────────────
    slack_ts         = Column(String, nullable=True)   # message timestamp for threading
    slack_channel    = Column(String, nullable=True)   # channel ID for threading
    channels_sent    = Column(ARRAY(String), nullable=False, default=list)

    created_at       = Column(DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(tz=timezone.utc))
    updated_at       = Column(DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(tz=timezone.utc),
                              onupdate=lambda: datetime.now(tz=timezone.utc))
