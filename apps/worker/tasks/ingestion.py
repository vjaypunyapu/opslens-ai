"""
OpsLens AI – Document Processing Pipeline
Normalise → Deduplicate → Chunk → Embed → Upsert to Qdrant
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import tiktoken
from celery import shared_task
from celery.utils.log import get_task_logger
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from ..config import settings
from ..db import AsyncSession
from ..models.document import CanonicalDocument

logger = get_task_logger(__name__)

# ── Singletons ───────────────────────────────────────────────────────────────
class _Tokenizer(Protocol):
    def encode(self, text: str) -> list[int]: ...
    def decode(self, tokens: list[int]) -> str: ...


class _FallbackTokenizer:
    @staticmethod
    def encode(text: str) -> list[int]:
        return [ord(ch) for ch in text]

    @staticmethod
    def decode(tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens)


def _build_tokenizer() -> _Tokenizer:
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        logger.warning("Failed to load tiktoken cl100k_base; using fallback tokenizer: %s", exc)
        return _FallbackTokenizer()


_enc = _build_tokenizer()
_openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
_qdrant = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)

CHUNK_TOKENS   = 512
OVERLAP_TOKENS = 50
EMBED_BATCH    = 100
EMBED_MODEL    = "text-embedding-3-small"
EMBED_DIMS     = 1536


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
                    f"Labels: {', '.join(label.get('name','') for label in raw.get('labels', []))}",
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


NORMALIZERS = {
    "slack":          SlackNormalizer,
    "jira":           JiraNormalizer,
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


# ── Stage 3: Chunking ─────────────────────────────────────────────────────────
def chunk_text(text: str) -> list[str]:
    """Split text into overlapping token-bounded chunks."""
    tokens = _enc.encode(text)
    if not tokens:
        return []
    chunks, i = [], 0
    while i < len(tokens):
        chunk_tokens = tokens[i: i + CHUNK_TOKENS]
        chunks.append(_enc.decode(chunk_tokens))
        i += CHUNK_TOKENS - OVERLAP_TOKENS
    return chunks


# ── Stage 4 + 5: Embed & Upsert ──────────────────────────────────────────────
async def embed_and_upsert(
    doc: CanonicalDocument,
    chunks: list[str],
    tenant_id: str,
) -> None:
    """Embed chunks and upsert each batch immediately to avoid memory spikes."""
    collection_name = f"opslens_{tenant_id}"

    # Ensure collection exists
    existing = [c.name for c in _qdrant.get_collections().collections]
    if collection_name not in existing:
        _qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=EMBED_DIMS, distance=Distance.COSINE),
        )

    upserted_points = 0

    for batch_start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[batch_start: batch_start + EMBED_BATCH]
        response = await _openai.embeddings.create(model=EMBED_MODEL, input=batch)
        batch_points: list[PointStruct] = []

        for local_i, emb_obj in enumerate(response.data):
            chunk_idx = batch_start + local_i
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
                        "content_preview": batch[local_i][:400],
                    },
                )
            )

        if batch_points:
            _qdrant.upsert(collection_name=collection_name, points=batch_points, wait=True)
            upserted_points += len(batch_points)

    if upserted_points:
        logger.info("Upserted %d points to %s", upserted_points, collection_name)


# ── Celery task ───────────────────────────────────────────────────────────────
@shared_task(bind=True, max_retries=3, default_retry_delay=60, name="ingestion.process_document")
def process_document(self, doc_id: str, tenant_id: str) -> dict:
    """
    Process a single CanonicalDocument: chunk → embed → upsert.
    Called after the normalizer has created/updated the DB record.
    """
    import asyncio
    try:
        return asyncio.run(_process_async(doc_id, tenant_id))
    except Exception as exc:
        logger.exception("process_document failed for %s: %s", doc_id, exc)
        raise self.retry(exc=exc) from exc


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

        chunks = chunk_text(content)
        await embed_and_upsert(doc, chunks, tenant_id)

        doc.embedding_status = "done"
        doc.chunk_count = len(chunks)
        await db.commit()

        return {"status": "ok", "doc_id": doc_id, "chunks": len(chunks)}


@shared_task(name="ingestion.process_staging_batch")
def process_staging_batch(tenant_id: str, source_type: str, limit: int = 500) -> dict:
    """
    Pulled by Celery Beat every sync cycle.
    Reads unprocessed records from the Airbyte staging schema,
    normalises them, and dispatches process_document tasks.
    """
    import asyncio
    return asyncio.run(_process_staging_batch(tenant_id, source_type, limit))


async def _process_staging_batch(tenant_id: str, source_type: str, limit: int) -> dict:
    """
    Reads raw records queued via the webhook (stored in opslens.ingestion_queue),
    normalises them into CanonicalDocument rows, then dispatches process_document
    tasks for embedding.  The table is created by main.py create_all.
    """
    import sqlalchemy as sa

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
                record: RawRecord = normalizer_cls.normalise(raw)
                chash = content_hash(record)

                # Check for existing doc with same hash — skip if unchanged
                existing = (await db.execute(
                    sa.text(
                        "SELECT id FROM opslens.canonical_documents "
                        "WHERE tenant_id=:tid AND source_id=:sid AND content_hash=:ch"
                    ),
                    {"tid": tenant_id, "sid": record.source_id, "ch": chash},
                )).scalar_one_or_none()

                if existing:
                    skipped += 1
                else:
                    from ..models.document import CanonicalDocument
                    doc = CanonicalDocument(
                        tenant_id=uuid.UUID(tenant_id),
                        source_type=record.source_type,
                        source_id=record.source_id,
                        content_hash=chash,
                        title=record.title,
                        content=record.content,
                        author=record.author,
                        url=record.url,
                        doc_metadata=record.metadata,
                        source_created_at=record.created_at,
                        source_updated_at=record.updated_at,
                        embedding_status="pending",
                    )
                    db.add(doc)
                    await db.flush()          # get doc.id
                    process_document.delay(str(doc.id), tenant_id)
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
