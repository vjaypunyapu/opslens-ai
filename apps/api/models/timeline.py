"""
OpsLens AI — Delivery Risk Timeline Model
==========================================
A unified event store that merges signals from all connected sources into a
single chronological feed. Used to answer the most critical incident question:
"What changed right before this spike?"

Event sources:
    github_pr       — Pull request opened, merged, closed
    github_commit   — Individual commit (branch push or merge commit)
    github_deploy   — GitHub Actions deployment workflow (started / succeeded / failed)
    jira_issue      — Issue status transitions (e.g. In Progress → Done → Reopened)
    log_anomaly     — Exception spike detected by fast_scan (from log_fast_alert)
    rrt_brief       — RRT Brief created / status changed
    manual          — User-recorded event (planned maintenance, config change, etc.)

Querying:
    - Fetch all events in a time window around an incident
    - Filter by source_type, service, or team
    - The correlation_window helper on the router can auto-center on an RRT brief
      and return ±N minutes of surrounding events

Indexing strategy:
    - (tenant_id, occurred_at) — primary time-range scans
    - (tenant_id, source_type) — filter by source
    - (tenant_id, service)     — filter by service/repo
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from apps.api.db.base import Base


class TimelineEvent(Base):
    """
    One event in the delivery risk timeline.

    The `metadata` JSONB field holds source-specific fields so we don't need
    to alter the schema every time a new source type is added:

        github_pr:
            { pr_number, pr_url, author, branch, base_branch, additions, deletions,
              files_changed, merged_by }

        github_commit:
            { sha, short_sha, message, author, branch, url, files_changed }

        github_deploy:
            { workflow_name, run_id, run_url, environment, triggered_by,
              status: started|succeeded|failed|cancelled }

        jira_issue:
            { issue_key, issue_url, summary, from_status, to_status,
              assignee, priority, issue_type }

        log_anomaly:
            { error_signature, error_count, window_minutes, containers,
              routed_teams, rrt_brief_id }

        rrt_brief:
            { brief_id, brief_url, from_status, to_status, title }

        manual:
            { description, recorded_by }
    """
    __tablename__ = "timeline_events"
    __table_args__ = {"schema": "opslens"}

    id           = Column(String, primary_key=True)
    tenant_id    = Column(String, nullable=False, index=True)

    # ── Classification ────────────────────────────────────────────────────────
    source_type  = Column(String, nullable=False, index=True)
    # github_pr | github_commit | github_deploy | jira_issue |
    # log_anomaly | rrt_brief | manual

    # ── Primary sort key ─────────────────────────────────────────────────────
    occurred_at  = Column(DateTime(timezone=True), nullable=False, index=True)

    # ── Human-readable summary (shown in timeline card) ───────────────────────
    title        = Column(String, nullable=False)
    # e.g. "PR #87 merged: Remove tenant_id default"
    # e.g. "Deploy: payment-service → production (FAILED)"
    # e.g. "Exception spike: TypeError in checkout-worker (×14)"

    description  = Column(Text, nullable=True)
    # Optional extra detail shown in expanded card

    # ── Routing / ownership ───────────────────────────────────────────────────
    service      = Column(String, nullable=True, index=True)
    # repo name, container, or Jira project key

    team         = Column(String, nullable=True)
    # owner_team from routing rule or GitHub CODEOWNERS

    actor        = Column(String, nullable=True)
    # GitHub login, Jira user, or user_id who posted the event

    # ── Risk signal ───────────────────────────────────────────────────────────
    is_anomaly   = Column(Boolean, nullable=False, default=False)
    # True for log_anomaly, deploy failures, rrt_brief creation
    # Used to highlight risk points on the timeline UI

    severity     = Column(String, nullable=True)
    # critical | high | medium | low | info — optional risk classification

    # ── External links ────────────────────────────────────────────────────────
    external_url = Column(String, nullable=True)
    # PR URL, deploy run URL, Jira issue URL, etc.

    # ── Source-specific fields ────────────────────────────────────────────────
    event_metadata = Column("metadata", JSONB, nullable=False, default=dict)

    # ── Traceability ─────────────────────────────────────────────────────────
    source_id    = Column(String, nullable=True)
    # Dedup key — e.g. github PR node_id, Jira changelog id
    # Webhooks use this to upsert rather than duplicate

    created_at   = Column(DateTime(timezone=True), nullable=False,
                          default=lambda: datetime.now(tz=timezone.utc))
