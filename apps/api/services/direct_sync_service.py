"""
OpsLens AI — Direct Sync Service
==================================
Fetches data directly from source APIs (no Airbyte required).
Used in dev/local mode when an integration has no airbyte_connection_id.

Supported sources: github, jira, slack, zendesk, hubspot
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy as sa
import tiktoken
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from ..config import settings
from ..db.models import CanonicalDocument, Integration
from ..db.session import AsyncSessionFactory as async_session_factory
from ..utils.crypto import decrypt_credentials
from ..utils.logging import get_logger

logger = get_logger(__name__)

# ── Embedding constants ───────────────────────────────────────────────────────
CHUNK_TOKENS   = 512
OVERLAP_TOKENS = 50
EMBED_BATCH    = 50
EMBED_MODEL    = "text-embedding-3-small"
EMBED_DIMS     = 1536

_enc     = tiktoken.get_encoding("cl100k_base")
_openai  = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
_qdrant  = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)


# ── Normalised record ─────────────────────────────────────────────────────────
@dataclass
class RawRecord:
    source_type: str
    source_id:   str
    title:       str
    content:     str
    author:      str
    url:         str
    created_at:  datetime
    updated_at:  datetime
    metadata:    dict[str, Any] = field(default_factory=dict)


# ── Text chunking ─────────────────────────────────────────────────────────────
def _chunk_text(text: str) -> list[str]:
    tokens = _enc.encode(text)
    if not tokens:
        return []
    chunks, start = [], 0
    while start < len(tokens):
        end = min(start + CHUNK_TOKENS, len(tokens))
        chunks.append(_enc.decode(tokens[start:end]))
        start += CHUNK_TOKENS - OVERLAP_TOKENS
    return chunks


# ── Embed + upsert to Qdrant ──────────────────────────────────────────────────
async def _embed_and_upsert(doc: CanonicalDocument, chunks: list[str], tenant_id: str) -> None:
    collection = f"opslens_{tenant_id}"
    existing = [c.name for c in _qdrant.get_collections().collections]
    if collection not in existing:
        _qdrant.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=EMBED_DIMS, distance=Distance.COSINE),
        )

    points: list[PointStruct] = []
    for batch_start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[batch_start: batch_start + EMBED_BATCH]
        response = await _openai.embeddings.create(model=EMBED_MODEL, input=batch)
        for i, emb in enumerate(response.data):
            chunk_idx = batch_start + i
            points.append(PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc.id}:{chunk_idx}")),
                vector=emb.embedding,
                payload={
                    "tenant_id":       tenant_id,
                    "document_id":     str(doc.id),
                    "chunk_index":     chunk_idx,
                    "source_type":     doc.source_type,
                    "title":           doc.title or "",
                    "url":             doc.url or "",
                    "author":          doc.author or "",
                    "created_at":      doc.source_created_at.isoformat() if doc.source_created_at else None,
                    "content_preview": batch[i][:400],
                },
            ))

    if points:
        _qdrant.upsert(collection_name=collection, points=points, wait=True)
        logger.info("Upserted %d vectors to %s", len(points), collection)


# ── Save record to DB + embed ─────────────────────────────────────────────────
async def _save_and_embed(record: RawRecord, tenant_id: str) -> None:
    content_hash = hashlib.sha256(record.content.encode()).hexdigest()

    async with async_session_factory() as db:
        # Check for existing doc (deduplication)
        result = await db.execute(
            sa.select(CanonicalDocument).where(
                CanonicalDocument.tenant_id == uuid.UUID(tenant_id),
                CanonicalDocument.content_hash == content_hash,
            )
        )
        existing = result.scalar_one_or_none()
        if existing and existing.embedding_status == "done":
            return  # already processed

        if existing:
            doc = existing
        else:
            doc = CanonicalDocument(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                source_type=record.source_type,
                source_id=record.source_id,
                content_hash=content_hash,
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
            await db.commit()
            await db.refresh(doc)

        chunks = _chunk_text(record.content)
        if chunks:
            await _embed_and_upsert(doc, chunks, tenant_id)

        doc.embedding_status = "done"
        doc.chunk_count = len(chunks)
        await db.commit()


# ── GitHub fetcher ────────────────────────────────────────────────────────────
async def _fetch_github(creds: dict, tenant_id: str, integration_id: str) -> int:
    token = creds.get("access_token", "")
    org   = creds.get("org", "")
    if not token:
        raise ValueError("GitHub access_token missing from credentials")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    records: list[RawRecord] = []

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # List repos (org or user)
        if org:
            repos_resp = await client.get(f"https://api.github.com/orgs/{org}/repos",
                                          params={"per_page": 30, "sort": "updated"})
        else:
            repos_resp = await client.get("https://api.github.com/user/repos",
                                          params={"per_page": 30, "sort": "updated"})
        repos_resp.raise_for_status()
        repos = repos_resp.json()

        for repo in repos[:10]:  # cap at 10 repos per sync
            repo_name  = repo["full_name"]
            repo_url   = repo.get("html_url", "")
            repo_desc  = repo.get("description") or ""

            # Repo summary as a document
            records.append(RawRecord(
                source_type="github",
                source_id=f"repo:{repo['id']}",
                title=f"GitHub Repo: {repo_name}",
                content=f"Repository: {repo_name}\n{repo_desc}\n"
                        f"Stars: {repo.get('stargazers_count', 0)}  "
                        f"Open issues: {repo.get('open_issues_count', 0)}  "
                        f"Language: {repo.get('language', 'unknown')}",
                author=repo.get("owner", {}).get("login", ""),
                url=repo_url,
                created_at=datetime.fromisoformat(repo["created_at"].rstrip("Z")).replace(tzinfo=timezone.utc),
                updated_at=datetime.fromisoformat(repo["updated_at"].rstrip("Z")).replace(tzinfo=timezone.utc),
                metadata={"repo": repo_name},
            ))

            # Issues + PRs
            issues_resp = await client.get(
                f"https://api.github.com/repos/{repo_name}/issues",
                params={"state": "all", "per_page": 50, "sort": "updated"},
            )
            if issues_resp.status_code == 200:
                for issue in issues_resp.json():
                    body = issue.get("body") or ""
                    content = f"{issue['title']}\n\n{body}".strip()
                    if not content:
                        continue
                    kind = "PR" if issue.get("pull_request") else "Issue"
                    records.append(RawRecord(
                        source_type="github",
                        source_id=f"issue:{issue['id']}",
                        title=f"[{kind} #{issue['number']}] {issue['title']}",
                        content=content,
                        author=issue.get("user", {}).get("login", ""),
                        url=issue.get("html_url", ""),
                        created_at=datetime.fromisoformat(issue["created_at"].rstrip("Z")).replace(tzinfo=timezone.utc),
                        updated_at=datetime.fromisoformat(issue["updated_at"].rstrip("Z")).replace(tzinfo=timezone.utc),
                        metadata={"repo": repo_name, "state": issue.get("state"), "type": kind.lower()},
                    ))

    logger.info("GitHub direct sync: %d records for tenant %s", len(records), tenant_id)
    for rec in records:
        try:
            await _save_and_embed(rec, tenant_id)
        except Exception as exc:
            logger.warning("Skipping record %s: %s", rec.source_id, exc)

    return len(records)


# ── Jira fetcher ──────────────────────────────────────────────────────────────
async def _fetch_jira(creds: dict, tenant_id: str, integration_id: str) -> int:
    server_url = creds.get("server_url", "").rstrip("/")
    email      = creds.get("email", "")
    api_token  = creds.get("api_token", "")
    if not (server_url and email and api_token):
        raise ValueError("Jira credentials incomplete (need server_url, email, api_token)")

    auth    = (email, api_token)
    headers = {"Accept": "application/json"}
    records: list[RawRecord] = []

    async with httpx.AsyncClient(auth=auth, headers=headers, timeout=30) as client:
        start_at, max_results = 0, 50
        while True:
            resp = await client.get(
                f"{server_url}/rest/api/3/search",
                params={
                    "jql": "ORDER BY updated DESC",
                    "startAt": start_at,
                    "maxResults": max_results,
                    "fields": "summary,description,status,assignee,reporter,created,updated,issuetype,project",
                },
            )
            resp.raise_for_status()
            data   = resp.json()
            issues = data.get("issues", [])
            if not issues:
                break

            for issue in issues:
                f = issue.get("fields", {})
                desc = ""
                # Jira description can be Atlassian Document Format (ADF) or plain text
                raw_desc = f.get("description")
                if isinstance(raw_desc, str):
                    desc = raw_desc
                elif isinstance(raw_desc, dict):
                    # Extract plain text from ADF content blocks
                    def _extract_adf(node: dict) -> str:
                        if node.get("type") == "text":
                            return node.get("text", "")
                        return " ".join(_extract_adf(c) for c in node.get("content", []))
                    desc = _extract_adf(raw_desc)

                content = f"{f.get('summary', '')}\n\n{desc}".strip()
                if not content:
                    continue

                records.append(RawRecord(
                    source_type="jira",
                    source_id=issue["id"],
                    title=f"[{issue['key']}] {f.get('summary', '')}",
                    content=content,
                    author=(f.get("reporter") or {}).get("displayName", ""),
                    url=f"{server_url}/browse/{issue['key']}",
                    created_at=datetime.fromisoformat(f["created"].rstrip("Z")).replace(tzinfo=timezone.utc),
                    updated_at=datetime.fromisoformat(f["updated"].rstrip("Z")).replace(tzinfo=timezone.utc),
                    metadata={
                        "key":     issue["key"],
                        "status":  (f.get("status") or {}).get("name", ""),
                        "project": (f.get("project") or {}).get("key", ""),
                        "type":    (f.get("issuetype") or {}).get("name", ""),
                    },
                ))

            start_at += max_results
            if start_at >= data.get("total", 0) or len(records) >= 200:
                break

    logger.info("Jira direct sync: %d records for tenant %s", len(records), tenant_id)
    for rec in records:
        try:
            await _save_and_embed(rec, tenant_id)
        except Exception as exc:
            logger.warning("Skipping record %s: %s", rec.source_id, exc)

    return len(records)


# ── Slack fetcher ─────────────────────────────────────────────────────────────
async def _fetch_slack(creds: dict, tenant_id: str, integration_id: str) -> int:
    token = creds.get("bot_token", "")
    if not token:
        raise ValueError("Slack bot_token missing from credentials")

    headers = {"Authorization": f"Bearer {token}"}
    records: list[RawRecord] = []

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # List public channels
        ch_resp = await client.get("https://slack.com/api/conversations.list",
                                   params={"types": "public_channel", "limit": 20})
        ch_resp.raise_for_status()
        channels = ch_resp.json().get("channels", [])

        for ch in channels[:10]:
            ch_id   = ch["id"]
            ch_name = ch.get("name", ch_id)

            hist_resp = await client.get(
                "https://slack.com/api/conversations.history",
                params={"channel": ch_id, "limit": 100},
            )
            if not hist_resp.json().get("ok"):
                continue

            for msg in hist_resp.json().get("messages", []):
                text = msg.get("text", "").strip()
                if not text or msg.get("subtype"):
                    continue
                ts_float = float(msg.get("ts", 0))
                ts = datetime.fromtimestamp(ts_float, tz=timezone.utc)
                records.append(RawRecord(
                    source_type="slack",
                    source_id=msg["ts"],
                    title=f"#{ch_name} – {msg.get('user', 'unknown')}",
                    content=text,
                    author=msg.get("user", ""),
                    url="",
                    created_at=ts,
                    updated_at=ts,
                    metadata={"channel": ch_name},
                ))

    logger.info("Slack direct sync: %d records for tenant %s", len(records), tenant_id)
    for rec in records:
        try:
            await _save_and_embed(rec, tenant_id)
        except Exception as exc:
            logger.warning("Skipping record %s: %s", rec.source_id, exc)

    return len(records)


# ── HubSpot fetcher ───────────────────────────────────────────────────────────
async def _fetch_hubspot(creds: dict, tenant_id: str, integration_id: str) -> int:
    token = creds.get("access_token", "")
    if not token:
        raise ValueError("HubSpot access_token missing from credentials")

    headers = {"Authorization": f"Bearer {token}"}
    records: list[RawRecord] = []

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        for obj_type in ("deals", "contacts", "companies"):
            resp = await client.get(
                f"https://api.hubapi.com/crm/v3/objects/{obj_type}",
                params={"limit": 50, "properties": "name,dealname,firstname,lastname,email,description,createdate,hs_lastmodifieddate"},
            )
            if resp.status_code != 200:
                continue
            for obj in resp.json().get("results", []):
                props = obj.get("properties", {})
                name = props.get("dealname") or props.get("name") or \
                       f"{props.get('firstname','')} {props.get('lastname','')}".strip() or obj["id"]
                desc = props.get("description", "") or ""
                content = f"{name}\n{desc}".strip()
                if not content:
                    continue
                created_str = props.get("createdate", "")
                updated_str = props.get("hs_lastmodifieddate", "") or created_str
                try:
                    created_dt = datetime.fromisoformat(created_str.rstrip("Z")).replace(tzinfo=timezone.utc)
                    updated_dt = datetime.fromisoformat(updated_str.rstrip("Z")).replace(tzinfo=timezone.utc)
                except Exception:
                    created_dt = updated_dt = datetime.now(tz=timezone.utc)
                records.append(RawRecord(
                    source_type="hubspot",
                    source_id=f"{obj_type}:{obj['id']}",
                    title=f"[{obj_type.rstrip('s').capitalize()}] {name}",
                    content=content,
                    author="",
                    url=f"https://app.hubspot.com/{obj_type}/{obj['id']}",
                    created_at=created_dt,
                    updated_at=updated_dt,
                    metadata={"object_type": obj_type},
                ))

    logger.info("HubSpot direct sync: %d records for tenant %s", len(records), tenant_id)
    for rec in records:
        try:
            await _save_and_embed(rec, tenant_id)
        except Exception as exc:
            logger.warning("Skipping record %s: %s", rec.source_id, exc)

    return len(records)


# ── Dispatch ──────────────────────────────────────────────────────────────────
_FETCHERS = {
    "github":  _fetch_github,
    "jira":    _fetch_jira,
    "slack":   _fetch_slack,
    "hubspot": _fetch_hubspot,
}


async def run_direct_sync(integration_id: str, tenant_id: str) -> None:
    """
    Entry point called from the FastAPI background task.
    Decrypts credentials, fetches data from the source, embeds and stores it.
    Updates the integration status/stats when done.
    """
    async with async_session_factory() as db:
        result = await db.execute(
            sa.select(Integration).where(Integration.id == uuid.UUID(integration_id))
        )
        integration: Integration | None = result.scalar_one_or_none()
        if not integration:
            logger.error("direct_sync: integration %s not found", integration_id)
            return

        source_type = integration.source_type
        fetcher     = _FETCHERS.get(source_type)
        if not fetcher:
            logger.warning("direct_sync: no fetcher for %s", source_type)
            integration.error_message = f"Direct sync not implemented for {source_type}"
            integration.status = "error"
            await db.commit()
            return

        # Decrypt credentials
        raw_creds: dict = {}
        if integration.credentials:
            try:
                raw_creds = decrypt_credentials(integration.credentials)
            except Exception as exc:
                logger.error("direct_sync: failed to decrypt credentials: %s", exc)
                integration.error_message = "Credential decryption failed"
                integration.status = "error"
                await db.commit()
                return

        integration.status = "pending"
        await db.commit()

    # Run the fetch (outside the session to avoid long-held connections)
    try:
        count = await fetcher(raw_creds, str(tenant_id), integration_id)
    except Exception as exc:
        logger.exception("direct_sync failed for %s/%s: %s", source_type, integration_id, exc)
        async with async_session_factory() as db:
            result = await db.execute(sa.select(Integration).where(Integration.id == uuid.UUID(integration_id)))
            intg = result.scalar_one_or_none()
            if intg:
                intg.status = "error"
                intg.error_message = str(exc)
                await db.commit()
        return

    # Update stats
    async with async_session_factory() as db:
        result = await db.execute(sa.select(Integration).where(Integration.id == uuid.UUID(integration_id)))
        intg = result.scalar_one_or_none()
        if intg:
            intg.status = "active"
            intg.total_records = (intg.total_records or 0) + count
            intg.last_synced_at = datetime.now(tz=timezone.utc)
            intg.error_message = None
            await db.commit()

    logger.info("direct_sync complete: %s synced %d records", source_type, count)
