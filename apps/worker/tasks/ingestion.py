"""
OpsLens AI – Document Processing Pipeline
Normalise → Deduplicate → Chunk → Embed → Upsert to Qdrant
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import tiktoken
from celery import shared_task
from celery.utils.log import get_task_logger
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance

from ..config import settings
from ..db import AsyncSession
from ..models.document import CanonicalDocument
from ..async_utils import run_async as _run_async
from ..structural_parser import (
    StructuralChunk,
    structural_parse,
    generate_hyde_questions,
)

logger = get_task_logger(__name__)

# ── Singletons ───────────────────────────────────────────────────────────────
_enc = tiktoken.get_encoding("cl100k_base")
_openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
_qdrant = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)

CHUNK_TOKENS   = 512
OVERLAP_TOKENS = 50
EMBED_BATCH    = 100
EMBED_MODEL    = "text-embedding-3-small"
EMBED_DIMS     = 1536

# ── Storage optimisation constants ───────────────────────────────────────────
# Log-type source types that should be filtered by severity level.
# Contextual sources (jira, slack, github, etc.) are never filtered.
_LOG_SOURCE_TYPES = frozenset({
    "elasticsearch", "datadog", "cloudwatch", "gcp", "splunk", "azuremonitor",
})

# Severity ranking — used to enforce INGEST_LOG_MIN_LEVEL.
_LEVEL_RANK: dict[str, int] = {
    "DEBUG":    10,
    "TRACE":    10,
    "INFO":     20,
    "NOTICE":   25,
    "WARNING":  30,
    "WARN":     30,
    "ERROR":    40,
    "CRITICAL": 50,
    "FATAL":    50,
    "ALERT":    50,
    "EMERGENCY":60,
}

def _min_level_rank() -> int:
    """Return the numeric rank for the configured INGEST_LOG_MIN_LEVEL."""
    level = (settings.INGEST_LOG_MIN_LEVEL or "WARNING").upper().strip()
    return _LEVEL_RANK.get(level, _LEVEL_RANK["WARNING"])


def _should_ingest(record: "RawRecord") -> bool:
    """
    Returns False for log-type records whose severity is below the configured
    minimum level. Always returns True for contextual sources (Jira, Slack, etc.).
    """
    if record.source_type not in _LOG_SOURCE_TYPES:
        return True  # contextual source — always ingest
    level = str(record.metadata.get("level") or record.metadata.get("status") or "INFO").upper()
    rank = _LEVEL_RANK.get(level, _LEVEL_RANK["INFO"])
    return rank >= _min_level_rank()


def _truncate_log_content(record: "RawRecord") -> "RawRecord":
    """
    For log-type sources, cap content at INGEST_LOG_CONTENT_MAX_CHARS characters.
    Appends a truncation notice so engineers know the full message was longer.
    Does nothing for contextual sources or when the limit is disabled (0).
    """
    max_chars = settings.INGEST_LOG_CONTENT_MAX_CHARS
    if not max_chars or record.source_type not in _LOG_SOURCE_TYPES:
        return record
    if len(record.content) <= max_chars:
        return record
    record.content = (
        record.content[:max_chars]
        + f"\n… [truncated — {len(record.content) - max_chars} chars omitted]"
    )
    return record


# ── Data models ──────────────────────────────────────────────────────────────
@dataclass
class RawRecord:
    """Normalised record coming out of the Airbyte staging table."""
    source_type: str
    source_id: str
    title: str
    content: str
    author: str
    url: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Stage 1: Normalisation ────────────────────────────────────────────────────
class SlackNormalizer:
    SOURCE = "slack"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        return RawRecord(
            source_type=SlackNormalizer.SOURCE,
            source_id=raw["ts"],
            title=f"#{raw.get('channel_name', 'unknown')} – {raw.get('user', 'unknown')}",
            content=raw.get("text", ""),
            author=raw.get("user", ""),
            url=raw.get("permalink", ""),
            created_at=datetime.fromtimestamp(float(raw["ts"])),
            updated_at=datetime.fromtimestamp(float(raw.get("edited", {}).get("ts", raw["ts"]))),
            metadata={"channel": raw.get("channel_name"), "thread_ts": raw.get("thread_ts")},
        )


class JiraNormalizer:
    SOURCE = "jira"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        fields = raw.get("fields", {})
        return RawRecord(
            source_type=JiraNormalizer.SOURCE,
            source_id=raw["id"],
            title=f"[{raw.get('key')}] {fields.get('summary', '')}",
            content="\n\n".join(filter(None, [
                fields.get("description", ""),
                *[c.get("body", "") for c in fields.get("comment", {}).get("comments", [])],
            ])),
            author=fields.get("reporter", {}).get("displayName", ""),
            url=raw.get("self", ""),
            created_at=datetime.fromisoformat(fields["created"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(fields["updated"].replace("Z", "+00:00")),
            metadata={
                "status": fields.get("status", {}).get("name"),
                "priority": fields.get("priority", {}).get("name"),
                "labels": fields.get("labels", []),
                "issue_type": fields.get("issuetype", {}).get("name"),
            },
        )


class GDriveNormalizer:
    SOURCE = "gdrive"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        return RawRecord(
            source_type=GDriveNormalizer.SOURCE,
            source_id=raw["id"],
            title=raw.get("name", "Untitled"),
            content=raw.get("extracted_text", ""),   # pre-extracted by Airbyte or a custom step
            author=raw.get("owners", [{}])[0].get("displayName", ""),
            url=raw.get("webViewLink", ""),
            created_at=datetime.fromisoformat(raw["createdTime"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(raw["modifiedTime"].replace("Z", "+00:00")),
            metadata={"mime_type": raw.get("mimeType"), "size": raw.get("size")},
        )


class GitHubNormalizer:
    SOURCE = "github"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        # Handles both commits and pull-request payloads from Airbyte
        if "sha" in raw:
            # Commit record
            commit = raw.get("commit", {})
            author = commit.get("author", {})
            return RawRecord(
                source_type=GitHubNormalizer.SOURCE,
                source_id=raw["sha"],
                title=f"Commit: {(commit.get('message', '') or '').splitlines()[0][:120]}",
                content="\n\n".join(filter(None, [
                    commit.get("message", ""),
                    f"Files changed: {raw.get('stats', {}).get('total', 0)}",
                    "\n".join(
                        f"  {f.get('status','?')} {f.get('filename','')}"
                        for f in raw.get("files", [])[:20]
                    ),
                ])),
                author=author.get("name") or author.get("login", ""),
                url=raw.get("html_url", ""),
                created_at=datetime.fromisoformat(
                    (author.get("date") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")
                ),
                updated_at=datetime.fromisoformat(
                    (author.get("date") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")
                ),
                metadata={
                    "repo": raw.get("repository", {}).get("full_name", ""),
                    "branch": raw.get("branch", ""),
                    "additions": raw.get("stats", {}).get("additions", 0),
                    "deletions": raw.get("stats", {}).get("deletions", 0),
                },
            )
        else:
            # Pull-request record
            user = raw.get("user", {})
            return RawRecord(
                source_type=GitHubNormalizer.SOURCE,
                source_id=str(raw.get("id", raw.get("number", ""))),
                title=f"PR #{raw.get('number')}: {raw.get('title', '')}",
                content="\n\n".join(filter(None, [
                    raw.get("body", "") or "",
                    f"State: {raw.get('state', 'unknown')}",
                    f"Labels: {', '.join(l.get('name','') for l in raw.get('labels', []))}",
                ])),
                author=user.get("login", ""),
                url=raw.get("html_url", ""),
                created_at=datetime.fromisoformat(
                    (raw.get("created_at") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")
                ),
                updated_at=datetime.fromisoformat(
                    (raw.get("updated_at") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")
                ),
                metadata={
                    "state":    raw.get("state"),
                    "merged":   raw.get("merged", False),
                    "repo":     raw.get("base", {}).get("repo", {}).get("full_name", ""),
                    "pr_number": raw.get("number"),
                },
            )


class ZendeskNormalizer:
    SOURCE = "zendesk"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        via = raw.get("via", {}).get("source", {})
        comments = raw.get("comments", [])
        body_parts = [raw.get("description", "")]
        for c in comments:
            if c.get("body"):
                body_parts.append(f"[Comment by {c.get('author_id','?')}]\n{c['body']}")
        return RawRecord(
            source_type=ZendeskNormalizer.SOURCE,
            source_id=str(raw["id"]),
            title=f"[Ticket #{raw.get('id')}] {raw.get('subject', 'No subject')}",
            content="\n\n".join(filter(None, body_parts)),
            author=str(raw.get("requester_id", "")),
            url=f"https://{raw.get('url','').split('/')[2]}/agent/tickets/{raw.get('id','')}",
            created_at=datetime.fromisoformat(
                (raw.get("created_at") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")
            ),
            updated_at=datetime.fromisoformat(
                (raw.get("updated_at") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")
            ),
            metadata={
                "status":   raw.get("status"),
                "priority": raw.get("priority"),
                "tags":     raw.get("tags", []),
                "channel":  via.get("rel", ""),
                "type":     raw.get("type"),
            },
        )


class ElasticsearchNormalizer:
    SOURCE = "elasticsearch"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        """Handles Elasticsearch/OpenSearch log entries from Airbyte or direct API."""
        source = raw.get("_source", raw)  # unwrap ES hit wrapper if present
        ts_raw = (
            source.get("@timestamp")
            or source.get("timestamp")
            or source.get("time")
            or "1970-01-01T00:00:00Z"
        )
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime(1970, 1, 1)

        level = (
            source.get("level")
            or source.get("log", {}).get("level")
            or source.get("severity", "info")
        ).upper() if isinstance(
            source.get("level") or source.get("log", {}).get("level") or source.get("severity", "info"),
            str
        ) else "INFO"

        message = (
            source.get("message")
            or source.get("msg")
            or source.get("log", {}).get("original", "")
            or ""
        )
        service = (
            source.get("service", {}).get("name")
            if isinstance(source.get("service"), dict)
            else source.get("service", source.get("fields", {}).get("service", ""))
        )
        doc_id = raw.get("_id") or source.get("_id") or source.get("id", "")

        return RawRecord(
            source_type=ElasticsearchNormalizer.SOURCE,
            source_id=str(doc_id),
            title=f"[{level}] {service + ': ' if service else ''}{message[:120]}",
            content=message,
            author=source.get("user", {}).get("name", "") if isinstance(source.get("user"), dict) else source.get("user", ""),
            url=source.get("url", {}).get("full", "") if isinstance(source.get("url"), dict) else source.get("url", ""),
            created_at=ts,
            updated_at=ts,
            metadata={
                "level":   level,
                "index":   raw.get("_index", ""),
                "service": service,
                "host":    source.get("host", {}).get("name", "") if isinstance(source.get("host"), dict) else source.get("host", ""),
                "trace_id": source.get("trace", {}).get("id", "") if isinstance(source.get("trace"), dict) else "",
                "error_type": source.get("error", {}).get("type", "") if isinstance(source.get("error"), dict) else "",
            },
        )


class DatadogNormalizer:
    SOURCE = "datadog"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        """Handles Datadog log events and monitor alert payloads."""
        # Datadog logs API response shape
        attrs = raw.get("attributes", raw)
        ts_raw = attrs.get("timestamp") or attrs.get("date") or "1970-01-01T00:00:00+00:00"
        try:
            # Datadog uses epoch ms sometimes
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw / 1000 if ts_raw > 1e10 else ts_raw)
            else:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime(1970, 1, 1)

        status = attrs.get("status", "info").upper()
        message = attrs.get("message") or attrs.get("content", {}).get("message", "") or ""
        service = attrs.get("service", "")
        host = attrs.get("host", "")

        # Monitor alert shape
        if "transition" in raw:
            message = raw.get("body", message)
            status = raw.get("transition", status).upper()

        return RawRecord(
            source_type=DatadogNormalizer.SOURCE,
            source_id=str(raw.get("id", "")),
            title=f"[{status}] {service + ': ' if service else ''}{message[:120]}",
            content=message,
            author=service or host,
            url=attrs.get("url", ""),
            created_at=ts,
            updated_at=ts,
            metadata={
                "status":  status,
                "service": service,
                "host":    host,
                "tags":    attrs.get("tags", []),
                "source":  attrs.get("source", attrs.get("ddsource", "")),
                "env":     attrs.get("env", ""),
                "trace_id": attrs.get("dd", {}).get("trace_id", "") if isinstance(attrs.get("dd"), dict) else "",
            },
        )


class CloudWatchNormalizer:
    SOURCE = "cloudwatch"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        """Handles AWS CloudWatch Logs events (logEvents) and Alarms."""
        # CloudWatch Logs Insights / Subscriptions shape
        if "logEvents" in raw:
            # Subscription filter batch — flatten first event as representative
            events = raw.get("logEvents", [])
            first = events[0] if events else {}
            ts_ms = first.get("timestamp", 0)
            ts = datetime.fromtimestamp(ts_ms / 1000) if ts_ms else datetime(1970, 1, 1)
            message = first.get("message", "")
            log_group = raw.get("logGroup", "")
            log_stream = raw.get("logStream", "")
            return RawRecord(
                source_type=CloudWatchNormalizer.SOURCE,
                source_id=first.get("id", str(ts_ms)),
                title=f"CloudWatch log: {log_group} – {message[:100]}",
                content="\n".join(e.get("message", "") for e in events[:50]),
                author=log_stream,
                url="",
                created_at=ts,
                updated_at=ts,
                metadata={
                    "log_group":  log_group,
                    "log_stream": log_stream,
                    "owner":      raw.get("owner", ""),
                    "event_count": len(events),
                },
            )

        # CloudWatch Alarm SNS notification shape
        if "AlarmName" in raw or "NewStateValue" in raw:
            ts_raw = raw.get("StateChangeTime", "1970-01-01T00:00:00Z")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime(1970, 1, 1)
            state = raw.get("NewStateValue", "UNKNOWN")
            alarm_name = raw.get("AlarmName", "")
            description = raw.get("AlarmDescription", "") or raw.get("NewStateReason", "")
            return RawRecord(
                source_type=CloudWatchNormalizer.SOURCE,
                source_id=alarm_name,
                title=f"[{state}] CloudWatch Alarm: {alarm_name}",
                content=description,
                author="cloudwatch",
                url="",
                created_at=ts,
                updated_at=ts,
                metadata={
                    "alarm_name":  alarm_name,
                    "state":       state,
                    "region":      raw.get("Region", ""),
                    "account_id":  raw.get("AWSAccountId", ""),
                    "namespace":   raw.get("Trigger", {}).get("Namespace", ""),
                    "metric":      raw.get("Trigger", {}).get("MetricName", ""),
                },
            )

        # Generic log record
        ts_raw = raw.get("timestamp", raw.get("Timestamp", "1970-01-01T00:00:00Z"))
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            ts = datetime(1970, 1, 1)
        message = raw.get("message", raw.get("Message", str(raw)))
        return RawRecord(
            source_type=CloudWatchNormalizer.SOURCE,
            source_id=raw.get("id", raw.get("eventId", str(hash(message)))),
            title=f"CloudWatch: {message[:120]}",
            content=message,
            author="cloudwatch",
            url="",
            created_at=ts,
            updated_at=ts,
            metadata={"log_group": raw.get("logGroup", ""), "log_stream": raw.get("logStream", "")},
        )


class SplunkNormalizer:
    SOURCE = "splunk"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        """Handles Splunk search results and HEC event payloads."""
        # Splunk search result shape: {"result": {...}, "offset": n}
        result = raw.get("result", raw)

        ts_raw = result.get("_time") or result.get("timestamp") or "1970-01-01T00:00:00+00:00"
        try:
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(float(ts_raw))
            else:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime(1970, 1, 1)

        raw_event = result.get("_raw", result.get("event", ""))
        if isinstance(raw_event, dict):
            raw_event = str(raw_event)

        source = result.get("source", result.get("index", ""))
        host = result.get("host", "")
        source_type = result.get("sourcetype", "")
        level = (result.get("log_level") or result.get("severity") or "INFO").upper()

        return RawRecord(
            source_type=SplunkNormalizer.SOURCE,
            source_id=result.get("_cd") or result.get("_serial") or str(hash(raw_event)),
            title=f"[{level}] Splunk/{source_type}: {raw_event[:120]}",
            content=raw_event,
            author=host,
            url="",
            created_at=ts,
            updated_at=ts,
            metadata={
                "source":      source,
                "sourcetype":  source_type,
                "host":        host,
                "index":       result.get("index", ""),
                "level":       level,
                "splunk_search": raw.get("sid", ""),
            },
        )


class AzureMonitorNormalizer:
    SOURCE = "azure_monitor"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        """Handles Azure Monitor activity logs and diagnostic log records."""
        ts_raw = raw.get("time") or raw.get("eventTimestamp") or "1970-01-01T00:00:00Z"
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            ts = datetime(1970, 1, 1)

        level = (raw.get("level") or raw.get("properties", {}).get("level", "Information")).capitalize()
        resource_id = raw.get("resourceId", "")
        resource_name = resource_id.split("/")[-1] if resource_id else ""
        props = raw.get("properties", {}) or {}
        message = (
            props.get("message")
            or props.get("statusMessage", "")
            or raw.get("operationName", {}).get("value", "")
            if isinstance(raw.get("operationName"), dict)
            else raw.get("operationName", "")
        )
        if isinstance(message, dict):
            message = str(message)

        return RawRecord(
            source_type=AzureMonitorNormalizer.SOURCE,
            source_id=raw.get("id", raw.get("correlationId", str(hash(message)))),
            title=f"[{level}] Azure/{resource_name}: {str(message)[:120]}",
            content=str(message) or str(props)[:1000],
            author=raw.get("caller", ""),
            url="",
            created_at=ts,
            updated_at=ts,
            metadata={
                "resource_id":    resource_id,
                "resource_group": raw.get("resourceGroupName", ""),
                "subscription":   raw.get("subscriptionId", ""),
                "operation":      raw.get("operationName", {}).get("value", "") if isinstance(raw.get("operationName"), dict) else raw.get("operationName", ""),
                "status":         raw.get("status", {}).get("value", "") if isinstance(raw.get("status"), dict) else raw.get("status", ""),
                "category":       raw.get("category", {}).get("value", "") if isinstance(raw.get("category"), dict) else raw.get("category", ""),
                "correlation_id": raw.get("correlationId", ""),
            },
        )


class GCPLoggingNormalizer:
    SOURCE = "gcp_logging"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        """Handles Google Cloud Logging (Stackdriver) log entries."""
        ts_raw = raw.get("timestamp") or raw.get("receiveTimestamp") or "1970-01-01T00:00:00Z"
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            ts = datetime(1970, 1, 1)

        severity = raw.get("severity", "DEFAULT").upper()
        log_name = raw.get("logName", "")
        resource = raw.get("resource", {})
        resource_type = resource.get("type", "")
        labels = resource.get("labels", {})
        service = labels.get("service_name") or labels.get("container_name") or labels.get("project_id", "")

        # Payload can be textPayload, jsonPayload, or protoPayload
        text_payload = raw.get("textPayload", "")
        json_payload = raw.get("jsonPayload", {})
        proto_payload = raw.get("protoPayload", {})
        if text_payload:
            message = text_payload
        elif json_payload:
            message = json_payload.get("message") or json_payload.get("msg") or str(json_payload)[:500]
        elif proto_payload:
            message = proto_payload.get("methodName", "") + " " + proto_payload.get("status", {}).get("message", "")
        else:
            message = ""

        return RawRecord(
            source_type=GCPLoggingNormalizer.SOURCE,
            source_id=raw.get("insertId", str(hash(message + ts_raw))),
            title=f"[{severity}] GCP/{resource_type}{': ' + service if service else ''}: {str(message)[:120]}",
            content=str(message),
            author=raw.get("protoPayload", {}).get("authenticationInfo", {}).get("principalEmail", ""),
            url="",
            created_at=ts,
            updated_at=ts,
            metadata={
                "log_name":      log_name,
                "severity":      severity,
                "resource_type": resource_type,
                "service":       service,
                "project_id":    labels.get("project_id", ""),
                "trace":         raw.get("trace", ""),
                "span_id":       raw.get("spanId", ""),
                "http_method":   raw.get("httpRequest", {}).get("requestMethod", "") if raw.get("httpRequest") else "",
                "http_status":   raw.get("httpRequest", {}).get("status", "") if raw.get("httpRequest") else "",
            },
        )


class JiraCommentNormalizer:
    """
    Handles records from Airbyte's `issue_comments` stream.
    Each row is a single Jira comment with its own author, timestamp, and body.
    Stored as a separate CanonicalDocument so comment text is independently
    searchable in RAG — critical for tickets where diagnosis lives in comments,
    not the description.
    """
    SOURCE = "jira"

    @staticmethod
    def normalise(raw: dict) -> RawRecord:
        issue_key = raw.get("issueKey") or raw.get("issue_key") or raw.get("issueId", "")
        body = raw.get("body", "") or raw.get("renderedBody", "")
        author = (
            raw.get("author", {}).get("displayName", "")
            or raw.get("updateAuthor", {}).get("displayName", "")
        )
        created_str = raw.get("created", "")
        updated_str = raw.get("updated", created_str)

        try:
            created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except Exception:
            created_at = datetime.now(tz=timezone.utc)
        try:
            updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except Exception:
            updated_at = created_at

        # Build a stable source_id so comment updates are deduped correctly
        comment_id = raw.get("id", raw.get("self", ""))
        source_id = f"comment:{comment_id}"

        return RawRecord(
            source_type=JiraCommentNormalizer.SOURCE,
            source_id=source_id,
            title=f"[{issue_key}] Comment by {author}",
            content=body,
            author=author,
            url=raw.get("self", ""),
            created_at=created_at,
            updated_at=updated_at,
            metadata={
                "issue_key": issue_key,
                "comment_id": comment_id,
                "record_type": "jira_comment",
            },
        )


NORMALIZERS = {
    "slack":          SlackNormalizer,
    "jira":           JiraNormalizer,
    "jira_comment":   JiraCommentNormalizer,
    "gdrive":         GDriveNormalizer,
    "github":         GitHubNormalizer,
    "zendesk":        ZendeskNormalizer,
    "elasticsearch":  ElasticsearchNormalizer,
    "datadog":        DatadogNormalizer,
    "cloudwatch":     CloudWatchNormalizer,
    "splunk":         SplunkNormalizer,
    "azure_monitor":  AzureMonitorNormalizer,
    "gcp_logging":    GCPLoggingNormalizer,
}


# ── Stage 2: Deduplication ────────────────────────────────────────────────────
def content_hash(record: RawRecord) -> str:
    payload = f"{record.source_type}:{record.source_id}:{record.content}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Stage 3: Chunking (structural — replaces naive chunk_text) ────────────────
# chunk_text() kept for backward-compat with any external callers; internally
# the pipeline now uses structural_parse() from structural_parser.py.
def chunk_text(text: str) -> list[str]:
    """Legacy flat chunker — preserved for backward compatibility only.
    New code should call structural_parse() directly."""
    tokens = _enc.encode(text)
    if not tokens:
        return []
    chunks, i = [], 0
    while i < len(tokens):
        chunk_tokens = tokens[i: i + CHUNK_TOKENS]
        chunks.append(_enc.decode(chunk_tokens))
        i += CHUNK_TOKENS - OVERLAP_TOKENS
    return chunks


# ── Stage 4 + 5: Embed & Upsert (accepts StructuralChunk list) ───────────────
async def embed_and_upsert(
    doc: CanonicalDocument,
    chunks: list[StructuralChunk],
    tenant_id: str,
) -> None:
    """
    Embed structural chunks and upsert to Qdrant in batches.

    Each Qdrant point now carries richer payload:
      heading        — breadcrumb of the section this chunk belongs to
      chunk_type     — "text" | "code" | "table"
      hyde_questions — list of hypothetical questions (from HyDE step)
      code_language  — programming language (code chunks only)
    """
    collection_name = f"opslens_{tenant_id}"

    # Ensure collection exists
    existing = [c.name for c in _qdrant.get_collections().collections]
    if collection_name not in existing:
        _qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=EMBED_DIMS, distance=Distance.COSINE),
        )

    upserted_points = 0

    # chunks is now list[StructuralChunk]; embed the .content field
    chunk_texts = [c.content for c in chunks]

    for batch_start in range(0, len(chunk_texts), EMBED_BATCH):
        batch_texts  = chunk_texts[batch_start: batch_start + EMBED_BATCH]
        batch_chunks = chunks[batch_start: batch_start + EMBED_BATCH]

        response = await _openai.embeddings.create(model=EMBED_MODEL, input=batch_texts)
        batch_points: list[PointStruct] = []

        for local_i, emb_obj in enumerate(response.data):
            chunk_idx = batch_start + local_i
            sc        = batch_chunks[local_i]
            batch_points.append(
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc.id}:{chunk_idx}")),
                    vector=emb_obj.embedding,
                    payload={
                        "tenant_id":       tenant_id,
                        "document_id":     str(doc.id),
                        "chunk_index":     chunk_idx,
                        "source_type":     doc.source_type,
                        "title":           doc.title,
                        "url":             doc.url,
                        "author":          doc.author,
                        "created_at":      doc.source_created_at.isoformat() if doc.source_created_at else None,
                        # structural metadata
                        "heading":         sc.heading,
                        "chunk_type":      sc.chunk_type,
                        "hyde_questions":  sc.hyde_questions,
                        "code_language":   sc.metadata.get("code_language", ""),
                        # preview uses raw_content (no heading prefix, no HyDE questions)
                        "content_preview": sc.raw_content[:400],
                    },
                )
            )

        if batch_points:
            _qdrant.upsert(collection_name=collection_name, points=batch_points, wait=True)
            upserted_points += len(batch_points)

    if upserted_points:
        logger.info("Upserted %d structural points to %s", upserted_points, collection_name)


# ── Celery task ───────────────────────────────────────────────────────────────
@shared_task(bind=True, max_retries=3, default_retry_delay=60, name="ingestion.process_document")
def process_document(self, doc_id: str, tenant_id: str) -> dict:
    """
    Process a single CanonicalDocument: chunk → embed → upsert.
    Called after the normalizer has created/updated the DB record.
    """
    try:
        return _run_async(_process_async(doc_id, tenant_id))
    except Exception as exc:
        logger.exception("process_document failed for %s: %s", doc_id, exc)
        raise self.retry(exc=exc)


async def _process_async(doc_id: str, tenant_id: str) -> dict:
    async with AsyncSession() as db:
        doc: CanonicalDocument | None = await db.get(CanonicalDocument, doc_id)
        if not doc:
            return {"status": "not_found", "doc_id": doc_id}
        if doc.embedding_status == "done":
            return {"status": "skipped", "doc_id": doc_id}

        content = doc.content or ""
        if not content.strip():
            doc.embedding_status = "done"
            doc.chunk_count = 0
            await db.commit()
            return {"status": "empty", "doc_id": doc_id}

        # ── Stage 3: Structural parse ──────────────────────────────────────
        # Respects headings, keeps code blocks and tables atomic,
        # inherits heading breadcrumb into each chunk's content & metadata.
        structural_chunks = structural_parse(content)
        logger.info(
            "Structural parse: doc=%s source=%s → %d chunks "
            "(%d text, %d code, %d table)",
            doc_id, doc.source_type,
            len(structural_chunks),
            sum(1 for c in structural_chunks if c.chunk_type == "text"),
            sum(1 for c in structural_chunks if c.chunk_type == "code"),
            sum(1 for c in structural_chunks if c.chunk_type == "table"),
        )

        # ── Stage 3b: HyDE — hypothetical questions per chunk ─────────────
        # One batched gpt-4o-mini call per document.
        # Appends questions to each chunk's .content before embedding.
        # Non-fatal: on failure the chunks are embedded without questions.
        try:
            structural_chunks = await generate_hyde_questions(
                structural_chunks, _openai,
            )
            hyde_count = sum(1 for c in structural_chunks if c.hyde_questions)
            logger.info("HyDE: generated questions for %d/%d chunks", hyde_count, len(structural_chunks))
        except Exception as hyde_exc:
            logger.warning("HyDE generation failed (proceeding without): %s", hyde_exc)

        # ── Stage 4+5: Embed & upsert ──────────────────────────────────────
        await embed_and_upsert(doc, structural_chunks, tenant_id)

        doc.embedding_status = "done"
        doc.chunk_count = len(structural_chunks)
        await db.commit()

        return {
            "status":      "ok",
            "doc_id":      doc_id,
            "chunks":      len(structural_chunks),
            "code_chunks": sum(1 for c in structural_chunks if c.chunk_type == "code"),
            "table_chunks":sum(1 for c in structural_chunks if c.chunk_type == "table"),
            "hyde_chunks": sum(1 for c in structural_chunks if c.hyde_questions),
        }


@shared_task(name="ingestion.process_staging_batch")
def process_staging_batch(tenant_id: str, source_type: str, limit: int = 500) -> dict:
    """
    Pulled by Celery Beat every sync cycle.
    Reads unprocessed records from the Airbyte staging schema,
    normalises them, and dispatches process_document tasks.
    """
    return _run_async(_process_staging_batch(tenant_id, source_type, limit))


async def _process_staging_batch(tenant_id: str, source_type: str, limit: int) -> dict:
    """
    Reads raw records queued via the webhook (stored in opslens.ingestion_queue),
    normalises them into CanonicalDocument rows, then dispatches process_document
    tasks for embedding.  The table is created by main.py create_all.
    """
    import sqlalchemy as sa

    # Map Airbyte stream names to normalizer keys
    # e.g. Airbyte emits source_type="jira" for all Jira streams, but the
    # staging record has a _airbyte_stream field to distinguish them.
    normalizer_cls = NORMALIZERS.get(source_type)
    if not normalizer_cls:
        return {"error": f"No normalizer for source_type={source_type}"}

    processed = skipped = errors = 0

    async with AsyncSession() as db:
        # Pull unprocessed records for this tenant + source from the queue table
        rows_result = await db.execute(
            sa.text("""
                SELECT id, raw_data
                FROM opslens.ingestion_queue
                WHERE tenant_id = :tid
                  AND source_type = :src
                  AND processed_at IS NULL
                ORDER BY created_at
                LIMIT :lim
            """),
            {"tid": tenant_id, "src": source_type, "lim": limit},
        )
        rows = rows_result.fetchall()

        for row in rows:
            queue_id = row.id
            raw: dict = row.raw_data  # JSONB → dict via asyncpg

            try:
                # Route Jira comment records to the dedicated normalizer
                effective_normalizer = normalizer_cls
                if source_type == "jira" and (
                    raw.get("_airbyte_stream") == "issue_comments"
                    or raw.get("record_type") == "jira_comment"
                    or "issueKey" in raw
                    or "issue_key" in raw
                ):
                    effective_normalizer = JiraCommentNormalizer

                record: RawRecord = effective_normalizer.normalise(raw)

                # ── Storage optimisation ──────────────────────────────────
                # 1. Drop log records below the configured minimum severity.
                if not _should_ingest(record):
                    skipped += 1
                    await db.execute(
                        sa.text(
                            "UPDATE opslens.ingestion_queue SET processed_at=now() "
                            "WHERE id=:qid"
                        ),
                        {"qid": queue_id},
                    )
                    continue

                # 2. Truncate oversized log content before storing.
                record = _truncate_log_content(record)
                # ─────────────────────────────────────────────────────────

                chash = content_hash(record)

                # ── Atomic upsert — race-condition-free dedup (Q4) ────────
                # Uses INSERT ... ON CONFLICT (tenant_id, source_type, source_id):
                #   - New document         → inserts, RETURNING id + inserted=true
                #   - Unchanged document   → DO NOTHING (content_hash matches),
                #                            RETURNING returns nothing → skipped
                #   - Updated document     → updates fields, RETURNING id + inserted=false
                #                            → re-queue for embedding
                # This replaces the previous SELECT + INSERT pattern which had a
                # race window where two concurrent workers could both insert the
                # same document, causing a constraint violation.
                upsert_result = (await db.execute(
                    sa.text("""
                        INSERT INTO opslens.canonical_documents
                            (id, tenant_id, source_type, source_id, content_hash,
                             title, content, author, url, doc_metadata,
                             source_created_at, source_updated_at, embedding_status)
                        VALUES
                            (gen_random_uuid(), :tid, :src_type, :src_id, :chash,
                             :title, :content, :author, :url, :metadata::jsonb,
                             :created_at, :updated_at, 'pending')
                        ON CONFLICT (tenant_id, source_type, source_id)
                        DO UPDATE SET
                            content_hash      = EXCLUDED.content_hash,
                            title             = EXCLUDED.title,
                            content           = EXCLUDED.content,
                            author            = EXCLUDED.author,
                            url               = EXCLUDED.url,
                            doc_metadata      = EXCLUDED.doc_metadata,
                            source_updated_at = EXCLUDED.source_updated_at,
                            embedding_status  = 'pending',
                            updated_at        = now()
                        WHERE opslens.canonical_documents.content_hash != EXCLUDED.content_hash
                        RETURNING id, (xmax = 0) AS inserted
                    """),
                    {
                        "tid":        tenant_id,
                        "src_type":   record.source_type,
                        "src_id":     record.source_id,
                        "chash":      chash,
                        "title":      record.title,
                        "content":    record.content,
                        "author":     record.author,
                        "url":        record.url,
                        "metadata":   json.dumps(record.metadata or {}),
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
                    },
                )).fetchone()

                if upsert_result is None:
                    # ON CONFLICT DO UPDATE WHERE clause was false → content unchanged → skip
                    skipped += 1
                else:
                    doc_id = str(upsert_result.id)
                    process_document.delay(doc_id, tenant_id)
                    processed += 1

                # Mark queue record as processed
                await db.execute(
                    sa.text(
                        "UPDATE opslens.ingestion_queue SET processed_at=now() "
                        "WHERE id=:qid"
                    ),
                    {"qid": queue_id},
                )

            except Exception as exc:
                logger.exception("Failed to process queue record %s: %s", queue_id, exc)
                errors += 1
                await db.execute(
                    sa.text(
                        "UPDATE opslens.ingestion_queue "
                        "SET error_msg=:msg "
                        "WHERE id=:qid"
                    ),
                    {"msg": str(exc)[:500], "qid": queue_id},
                )

        await db.commit()

    return {
        "tenant_id":   tenant_id,
        "source_type": source_type,
        "processed":   processed,
        "skipped":     skipped,
        "errors":      errors,
    }


# ── Embedding retry ───────────────────────────────────────────────────────────

@shared_task(name="ingestion.retry_pending_embeddings")
def retry_pending_embeddings() -> dict:
    """
    Find CanonicalDocuments stuck in embedding_status='pending' and re-dispatch
    process_document for each one. Runs on a schedule so transient OpenAI or
    Qdrant failures are automatically recovered without manual intervention.
    Capped at 100 docs per run to avoid overwhelming the queue.
    """
    async def _get_pending() -> list[str]:
        import sqlalchemy as sa
        async with AsyncSession() as db:
            rows = await db.execute(
                sa.select(CanonicalDocument.id)
                .where(CanonicalDocument.embedding_status == "pending")
                .limit(100)
            )
            return [str(r) for r in rows.scalars().all()]

    doc_ids = _run_async(_get_pending())
    for doc_id in doc_ids:
        process_document.delay(doc_id)
    logger.info("retry_pending_embeddings: dispatched %d docs", len(doc_ids))
    return {"dispatched": len(doc_ids)}
