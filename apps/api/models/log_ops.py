"""
OpsLens AI — Log Operations Models
=====================================
Two models that power production-grade log alerting:

KnownIssue
    Suppresses recurring alerts for acknowledged/tracked exceptions.
    Supports permanent suppression, time-boxed snooze, and Jira-linked
    auto-suppression (suppress while the ticket is open).

AlertRoutingRule
    Routes log alerts to the correct team based on error content.
    Matches against error text using keywords or regex patterns.
    Each rule maps to a specific team's Slack webhook / email list.
    Multiple rules can match a single exception (fan-out).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from apps.api.db.base import Base


class KnownIssue(Base):
    """
    A suppression entry for a recurring exception.

    Fields:
        signature       — md5 hash of the normalised exception line (matches
                          ErrorGroup.signature from log_fast_alert.py)
        description     — human note: "tracked in OPS-142, fix in v2.4"
        suppressed_by   — user_id who created the suppression
        suppress_until  — NULL = permanent; datetime = snooze expiry
        jira_ticket_key — e.g. "OPS-142"; if set, auto-suppress while ticket
                          is in a non-Done status (requires Jira credentials)
        match_pattern   — optional regex/keyword override (e.g. "KeyError.*tenant_id")
                          use when you want to suppress by error text rather than hash
        is_active       — soft-delete flag
    """
    __tablename__ = "known_issues"
    __table_args__ = {"schema": "opslens"}

    id              = Column(String, primary_key=True)
    tenant_id       = Column(String, nullable=False, index=True)
    signature       = Column(String, nullable=True, index=True)  # hash-based match
    match_pattern   = Column(String, nullable=True)              # regex/keyword match
    description     = Column(Text, nullable=True)
    suppressed_by   = Column(String, nullable=True)              # user_id
    suppress_until  = Column(DateTime(timezone=True), nullable=True)  # NULL = permanent
    jira_ticket_key = Column(String, nullable=True)              # e.g. "OPS-142"
    hit_count       = Column(Integer, nullable=False, default=0) # times suppressed
    last_hit_at     = Column(DateTime(timezone=True), nullable=True)
    is_active       = Column(Boolean, nullable=False, default=True)
    created_at      = Column(DateTime(timezone=True), nullable=False,
                             default=lambda: datetime.now(tz=timezone.utc))
    updated_at      = Column(DateTime(timezone=True), nullable=False,
                             default=lambda: datetime.now(tz=timezone.utc),
                             onupdate=lambda: datetime.now(tz=timezone.utc))


class AlertRoutingRule(Base):
    """
    Routes log alerts to the correct team based on error content.

    Matching logic (evaluated in priority order):
        1. service_patterns  — match if the log line contains any of these
                               keywords/regex (e.g. ["payment", "stripe", "checkout"])
        2. error_patterns    — match on the exception class/message
                               (e.g. ["PaymentError", "StripeTimeout"])
        3. source_containers — match on Docker container name
                               (e.g. ["payment-service", "billing-worker"])

    If match_all=False (default), any one pattern match triggers the rule.
    If match_all=True, ALL non-empty pattern lists must have at least one match.

    Notification targets:
        slack_webhook       — team-specific Slack channel webhook
        email_recipients    — list of team email addresses
        pagerduty_key       — PagerDuty routing key (future)

    Fields:
        priority            — lower = evaluated first (default 100)
        stop_on_match       — if True, no further rules are evaluated after this
                              one matches (useful for an "ops-all" catch-all at 999)
    """
    __tablename__ = "alert_routing_rules"
    __table_args__ = {"schema": "opslens"}

    id                  = Column(String, primary_key=True)
    tenant_id           = Column(String, nullable=False, index=True)
    team_name           = Column(String, nullable=False)          # e.g. "Payments Team"
    description         = Column(Text, nullable=True)

    # Matching criteria (JSONB arrays of strings — keywords or regex)
    service_patterns    = Column(ARRAY(String), nullable=False, default=list)
    error_patterns      = Column(ARRAY(String), nullable=False, default=list)
    source_containers   = Column(ARRAY(String), nullable=False, default=list)
    match_all           = Column(Boolean, nullable=False, default=False)

    # Routing targets
    slack_webhook       = Column(String, nullable=True)
    email_recipients    = Column(ARRAY(String), nullable=False, default=list)
    pagerduty_key       = Column(String, nullable=True)           # future

    # Control
    priority            = Column(Integer, nullable=False, default=100)
    stop_on_match       = Column(Boolean, nullable=False, default=False)
    is_active           = Column(Boolean, nullable=False, default=True)

    created_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc))
    updated_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(tz=timezone.utc),
                                 onupdate=lambda: datetime.now(tz=timezone.utc))
