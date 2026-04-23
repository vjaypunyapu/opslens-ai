"""
OpsLens AI — Query Intent Detection
=====================================
Fast, regex/keyword-based classifier that runs before vector search so we can
route queries to the right handler instead of always doing semantic retrieval.

Intent types
------------
"id_lookup"   — query contains an exact identifier (PR #123, JIRA-456, sha)
"aggregate"   — user wants a count/total (how many open issues?)
"semantic"    — default: use hybrid vector+BM25 retrieval

Filter hints (extracted regardless of intent type)
---------------------------------------------------
  state       — "open" | "closed" | "merged"
  author      — person name/login
  source_type — "github" | "jira" | "slack" | ...
  since       — datetime lower bound (date range queries)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal


IntentType = Literal["id_lookup", "aggregate", "semantic"]


@dataclass
class QueryIntent:
    type: IntentType = "semantic"
    # For id_lookup: the raw identifier string as typed by the user
    id_string: str | None = None
    # For aggregate: what to count
    count: bool = False
    # Structured filters to narrow the candidate set before ranking
    filters: dict[str, str] = field(default_factory=dict)


# ── Patterns ──────────────────────────────────────────────────────────────────

# Jira-style ticket: PROJECT-123, PROJ-4567
_JIRA_ID_RE = re.compile(r'\b([A-Z]{2,10}-\d+)\b')
# GitHub PR/issue: #123, PR #123, issue #42
_GITHUB_NUM_RE = re.compile(r'\b(?:pr|issue|pull request)?\s*#\s*(\d+)\b', re.IGNORECASE)
# Commit SHA: 7+ hex chars (avoid matching random numbers)
_COMMIT_SHA_RE = re.compile(r'\b([0-9a-f]{7,40})\b', re.IGNORECASE)

_COUNT_RE = re.compile(
    r'\b(how many|how much|count of|number of|total( number)? of|'
    r'count|tally|quantit(?:y|ies))\b',
    re.IGNORECASE,
)

_STATE_MAP = {
    "open":       "open",
    "unresolved": "open",
    "unmerged":   "open",
    "pending":    "open",
    "closed":     "closed",
    "resolved":   "closed",
    "merged":     "merged",
    "done":       "closed",
    "completed":  "closed",
}

# "by Alice", "from Bob", "created by Charlie", "assigned to Dave"
_AUTHOR_RE = re.compile(
    r'\b(?:by|from|author(?:ed by)?|created by|opened by|submitted by|assigned to)\s+'
    r'([A-Za-z][A-Za-z0-9._-]{1,39})\b',
    re.IGNORECASE,
)

_SOURCE_MAP = {
    "github":    "github",
    "jira":      "jira",
    "slack":     "slack",
    "zendesk":   "zendesk",
    "hubspot":   "hubspot",
    "bitbucket": "bitbucket",
    "drive":     "google_drive",
    "google drive": "google_drive",
    "datadog":   "datadog",
    "cloudwatch":"cloudwatch",
    "railway":   "railway",
}

# Relative time keywords → (days_back)
_RELATIVE_TIME = {
    "today":      0,
    "yesterday":  1,
    "this week":  7,
    "last week":  7,
    "this month": 30,
    "last month": 30,
}
_LAST_N_DAYS_RE = re.compile(r'\blast\s+(\d+)\s+days?\b', re.IGNORECASE)


def detect(query: str) -> QueryIntent:
    """Classify a query and extract any filter hints.  Never raises."""
    intent = QueryIntent()
    q = query.strip()
    q_lower = q.lower()

    # ── ID lookup ─────────────────────────────────────────────────────────────
    m = _JIRA_ID_RE.search(q)
    if m:
        intent.type = "id_lookup"
        intent.id_string = m.group(1)
        return intent  # ID lookup overrides everything else

    m = _GITHUB_NUM_RE.search(q)
    if m:
        intent.type = "id_lookup"
        intent.id_string = f"#{m.group(1)}"
        return intent

    # Commit SHA only if the query explicitly mentions "commit"
    if "commit" in q_lower:
        m = _COMMIT_SHA_RE.search(q)
        if m:
            intent.type = "id_lookup"
            intent.id_string = m.group(1)
            return intent

    # ── Aggregate / count ─────────────────────────────────────────────────────
    if _COUNT_RE.search(q):
        intent.type = "aggregate"
        intent.count = True

    # ── Filters (applied to all non-id intents) ───────────────────────────────
    for keyword, state in _STATE_MAP.items():
        if re.search(rf'\b{re.escape(keyword)}\b', q_lower):
            intent.filters["state"] = state
            break

    m = _AUTHOR_RE.search(q)
    if m:
        intent.filters["author"] = m.group(1)

    for keyword, source_type in _SOURCE_MAP.items():
        if keyword in q_lower:
            intent.filters["source_type"] = source_type
            break

    # Date range → compute `since` timestamp
    for phrase, days_back in _RELATIVE_TIME.items():
        if phrase in q_lower:
            since = datetime.now(tz=timezone.utc) - timedelta(days=days_back)
            intent.filters["since"] = since.isoformat()
            break
    else:
        m = _LAST_N_DAYS_RE.search(q)
        if m:
            days_back = int(m.group(1))
            since = datetime.now(tz=timezone.utc) - timedelta(days=days_back)
            intent.filters["since"] = since.isoformat()

    return intent
