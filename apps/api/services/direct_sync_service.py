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

import urllib.parse as _urlparse

import httpx
import sqlalchemy as sa
import tiktoken
from openai import AsyncOpenAI

from ..config import settings
from ..db.models import CanonicalDocument, Integration
from ..db.session import AsyncSessionFactory as async_session_factory
from ..utils.crypto import decrypt_credentials
from ..utils.logging import get_logger
from .chunker import chunk_document, StructuredChunk

logger = get_logger(__name__)

# ── Embedding constants ───────────────────────────────────────────────────────
CHUNK_TOKENS   = 512
OVERLAP_TOKENS = 50
EMBED_BATCH    = 50

# Embedding model / dims depend on provider
if settings.LLM_PROVIDER == "ollama":
    EMBED_MODEL = settings.OLLAMA_EMBED_MODEL   # e.g. "nomic-embed-text"
    EMBED_DIMS  = 768                            # nomic-embed-text output dims
else:
    EMBED_MODEL = settings.OPENAI_EMBED_MODEL   # "text-embedding-3-small"
    EMBED_DIMS  = 1536

_enc = tiktoken.get_encoding("cl100k_base")

import os as _os
_openai_key = (
    _os.environ.get("OPENAI_TOKEN", "")
    or _os.environ.get("OPENAI_API_KEY", "")
    or (settings.OPENAI_API_KEY or "")
).strip()
_openai = AsyncOpenAI(api_key=_openai_key)


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


# ── Qdrant REST helpers (httpx direct — avoids qdrant-client auth bugs) ────────
def _qdrant_headers() -> dict[str, str]:
    import os
    h = {"Content-Type": "application/json"}
    # Try multiple env var names — Railway sometimes fails to inject specific names
    key = (
        os.environ.get("QDRANT_TOKEN", "")
        or os.environ.get("QDRANT_API_KEY", "")
        or (settings.QDRANT_API_KEY or "")
    ).strip()
    logger.info("qdrant_debug: url=%r key_len=%d QDRANT_TOKEN=%d QDRANT_API_KEY=%d",
                settings.QDRANT_URL, len(key),
                len(os.environ.get("QDRANT_TOKEN", "")),
                len(os.environ.get("QDRANT_API_KEY", "")))
    if key:
        h["api-key"] = key
    return h

def _qdrant_base() -> str:
    return settings.QDRANT_URL.rstrip("/")

async def _ensure_qdrant_collection(collection: str) -> None:
    base    = _qdrant_base()
    headers = _qdrant_headers()
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(f"{base}/collections/{collection}")
        if resp.status_code == 200:
            logger.info("qdrant: collection %s already exists", collection)
            return
        if resp.status_code == 404:
            logger.info("qdrant: creating collection %s dims=%d", collection, EMBED_DIMS)
            r = await client.put(
                f"{base}/collections/{collection}",
                json={"vectors": {"size": EMBED_DIMS, "distance": "Cosine"}},
            )
            r.raise_for_status()
            logger.info("qdrant: collection %s created", collection)
        else:
            logger.error("qdrant: unexpected %s checking collection: %s", resp.status_code, resp.text)
            resp.raise_for_status()

async def _upsert_qdrant_points(collection: str, points: list[dict]) -> None:
    base    = _qdrant_base()
    headers = _qdrant_headers()
    async with httpx.AsyncClient(headers=headers, timeout=60) as client:
        resp = await client.put(
            f"{base}/collections/{collection}/points?wait=true",
            json={"points": points},
        )
        resp.raise_for_status()


# ── Embed + upsert to Qdrant ──────────────────────────────────────────────────
async def _embed_and_upsert(
    doc: CanonicalDocument,
    structured_chunks: list[StructuredChunk],
    tenant_id: str,
) -> None:
    collection = f"opslens_{tenant_id}"
    logger.info("embed_and_upsert: doc=%s chunks=%d collection=%s", doc.id, len(structured_chunks), collection)

    try:
        await _ensure_qdrant_collection(collection)
    except Exception as exc:
        logger.error("embed_and_upsert: FAILED to ensure collection: %s", exc, exc_info=True)
        raise

    # Build text list — embed the content; if enriched also embed hypothetical questions
    # by prepending them so the vector captures question-to-question similarity
    def _embed_text(sc: StructuredChunk) -> str:
        parts = [sc.content]
        if sc.summary:
            parts.append(f"Summary: {sc.summary}")
        if sc.hypothetical_questions:
            parts.append("Questions: " + " | ".join(sc.hypothetical_questions))
        return " ".join(parts)

    texts = [_embed_text(sc) for sc in structured_chunks]

    points: list[dict] = []
    for batch_start in range(0, len(texts), EMBED_BATCH):
        batch_texts  = texts[batch_start: batch_start + EMBED_BATCH]
        batch_chunks = structured_chunks[batch_start: batch_start + EMBED_BATCH]

        if settings.LLM_PROVIDER == "ollama":
            ollama_resp = await httpx.AsyncClient(timeout=60).post(
                f"{settings.OLLAMA_URL}/api/embed",
                json={"model": EMBED_MODEL, "input": batch_texts},
            )
            ollama_resp.raise_for_status()
            embeddings_list = ollama_resp.json()["embeddings"]
        else:
            response = await _openai.embeddings.create(model=EMBED_MODEL, input=batch_texts)
            embeddings_list = [e.embedding for e in response.data]

        for i, vector in enumerate(embeddings_list):
            chunk_idx = batch_start + i
            sc        = batch_chunks[i]
            points.append({
                "id":     str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc.id}:{chunk_idx}")),
                "vector": vector,
                "payload": {
                    "page_content": sc.content,
                    "metadata": {
                        "tenant_id":              tenant_id,
                        "document_id":            str(doc.id),
                        "chunk_index":            chunk_idx,
                        "chunk_type":             sc.chunk_type,
                        "source_type":            sc.source_type,
                        "title":                  doc.title or "",
                        "url":                    doc.url or "",
                        "author":                 doc.author or "",
                        "created_at":             doc.source_created_at.isoformat() if doc.source_created_at else None,
                        "keywords":               sc.keywords,
                        "summary":                sc.summary,
                        "hypothetical_questions": sc.hypothetical_questions,
                    },
                },
            })

    if points:
        try:
            logger.info("embed_and_upsert: upserting %d points to %s", len(points), collection)
            await _upsert_qdrant_points(collection, points)
            logger.info("Upserted %d vectors to %s", len(points), collection)
        except Exception as exc:
            logger.error("embed_and_upsert: FAILED to upsert to Qdrant: %s", exc, exc_info=True)
            raise
    else:
        logger.warning("embed_and_upsert: no points to upsert for doc=%s (chunks were empty?)", doc.id)


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
                chunk_count=0,
            )
            db.add(doc)
            await db.commit()
            await db.refresh(doc)

        structured_chunks = await chunk_document(
            record.content,
            record.source_type,
            {**record.metadata, "title": record.title, "url": record.url},
        )
        if structured_chunks:
            await _embed_and_upsert(doc, structured_chunks, tenant_id)

        doc.embedding_status = "done"
        doc.chunk_count = len(structured_chunks)
        await db.commit()


# ── Batch save + embed for log sources ───────────────────────────────────────
# Log records (single short lines) don't benefit from the HyDE enrichment that
# chunk_document applies to contextual sources (GitHub issues, Jira tickets).
# Instead we use the simple _chunk_text helper + bulk API calls so that a 2000-
# record CloudWatch sync stays well within the 4-minute soft time limit.
#
# Reduction vs. the per-record path:
#   DB round-trips:       N SELECT + N INSERT + N UPDATE  →  1 + 1 + 1
#   Embedding API calls:  N (one per record)              →  ceil(total_chunks / EMBED_BATCH)
#   Qdrant upserts:       N (one per record)              →  ceil(total_points / 500)
#   GPT-4o-mini calls:   N (HyDE per record)             →  0  (skipped for logs)

_QDRANT_UPSERT_BATCH = 500  # Qdrant recommended max points per upsert request


async def _batch_save_and_embed_logs(records: list[RawRecord], tenant_id: str) -> int:
    """
    Batch-optimised save + embed path for log source records.

    Uses simple token chunking (no LLM enrichment) and collapses all DB and
    embedding I/O into O(1) calls regardless of how many records are in the list.

    Returns the number of *new* records saved (duplicates are skipped silently).
    """
    if not records:
        return 0

    tid = uuid.UUID(tenant_id)

    # ── Step 1: compute content hashes and deduplicate within this batch ──────
    # If the same log line appears twice in one fetch window, keep only the first.
    seen_in_batch: set[str] = set()
    hashed: list[tuple[str, RawRecord]] = []  # (content_hash, record)
    for rec in records:
        h = hashlib.sha256(rec.content.encode()).hexdigest()
        if h not in seen_in_batch:
            seen_in_batch.add(h)
            hashed.append((h, rec))

    all_hashes = [h for h, _ in hashed]

    # ── Step 2: one SELECT to find already-stored hashes ─────────────────────
    async with async_session_factory() as db:
        rows = await db.execute(
            sa.select(CanonicalDocument.content_hash).where(
                CanonicalDocument.tenant_id == tid,
                CanonicalDocument.content_hash.in_(all_hashes),
            )
        )
        existing_hashes: set[str] = {row[0] for row in rows.all()}

    # ── Step 3: build new doc objects for records not yet in the DB ───────────
    new_rows: list[tuple[str, RawRecord, CanonicalDocument]] = []
    for content_hash, rec in hashed:
        if content_hash in existing_hashes:
            continue
        doc = CanonicalDocument(
            id=uuid.uuid4(),
            tenant_id=tid,
            source_type=rec.source_type,
            source_id=rec.source_id,
            content_hash=content_hash,
            title=rec.title,
            content=rec.content,
            author=rec.author,
            url=rec.url,
            doc_metadata=rec.metadata,
            source_created_at=rec.created_at,
            source_updated_at=rec.updated_at,
            embedding_status="pending",
            chunk_count=0,
        )
        new_rows.append((content_hash, rec, doc))

    if not new_rows:
        logger.info(
            "batch_log_embed: all %d records already stored for tenant %s",
            len(records), tenant_id,
        )
        return 0

    # ── Step 4: bulk insert all new docs in one transaction ───────────────────
    async with async_session_factory() as db:
        db.add_all([doc for _, _, doc in new_rows])
        await db.commit()

    # ── Step 5: simple token chunking — no HyDE/LLM calls ────────────────────
    # Log lines are typically 1 chunk each (< 512 tokens).  _chunk_text is a
    # pure-Python tiktoken split — no network I/O.
    all_texts: list[str] = []
    all_meta: list[tuple[uuid.UUID, int, CanonicalDocument]] = []  # (doc_id, chunk_idx, doc)
    doc_chunk_counts: dict[uuid.UUID, int] = {}

    for _, rec, doc in new_rows:
        chunks = _chunk_text(rec.content)
        if not chunks:
            chunks = [rec.content]  # always keep at least the raw content
        doc_chunk_counts[doc.id] = len(chunks)
        for idx, chunk_text in enumerate(chunks):
            all_texts.append(chunk_text)
            all_meta.append((doc.id, idx, doc))

    # ── Step 6: embed all chunks — ceil(N/EMBED_BATCH) API calls total ────────
    collection = f"opslens_{tenant_id}"
    await _ensure_qdrant_collection(collection)

    all_points: list[dict] = []
    for batch_start in range(0, len(all_texts), EMBED_BATCH):
        batch_texts = all_texts[batch_start: batch_start + EMBED_BATCH]
        batch_meta  = all_meta[batch_start: batch_start + EMBED_BATCH]

        if settings.LLM_PROVIDER == "ollama":
            resp = await httpx.AsyncClient(timeout=60).post(
                f"{settings.OLLAMA_URL}/api/embed",
                json={"model": EMBED_MODEL, "input": batch_texts},
            )
            resp.raise_for_status()
            vectors = resp.json()["embeddings"]
        else:
            resp = await _openai.embeddings.create(model=EMBED_MODEL, input=batch_texts)
            vectors = [e.embedding for e in resp.data]

        for i, vector in enumerate(vectors):
            doc_id, chunk_idx, doc = batch_meta[i]
            all_points.append({
                "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}:{chunk_idx}")),
                "vector": vector,
                "payload": {
                    "page_content": batch_texts[i],
                    "metadata": {
                        "tenant_id":   tenant_id,
                        "document_id": str(doc_id),
                        "chunk_index": chunk_idx,
                        "chunk_type":  "log_line",
                        "source_type": doc.source_type,
                        "title":       doc.title or "",
                        "url":         doc.url or "",
                        "author":      doc.author or "",
                        "created_at":  doc.source_created_at.isoformat() if doc.source_created_at else None,
                    },
                },
            })

    # ── Step 7: bulk upsert to Qdrant in batches of _QDRANT_UPSERT_BATCH ─────
    for batch_start in range(0, len(all_points), _QDRANT_UPSERT_BATCH):
        batch = all_points[batch_start: batch_start + _QDRANT_UPSERT_BATCH]
        await _upsert_qdrant_points(collection, batch)

    logger.info(
        "batch_log_embed: upserted %d vectors (%d docs) to %s",
        len(all_points), len(new_rows), collection,
    )

    # ── Step 8: bulk update embedding_status + chunk_count in one transaction ─
    async with async_session_factory() as db:
        for _, _, doc in new_rows:
            await db.execute(
                sa.update(CanonicalDocument)
                .where(CanonicalDocument.id == doc.id)
                .values(
                    embedding_status="done",
                    chunk_count=doc_chunk_counts.get(doc.id, 0),
                )
            )
        await db.commit()

    logger.info(
        "batch_log_embed: saved %d new records for tenant %s (%d duplicates skipped)",
        len(new_rows), tenant_id, len(records) - len(new_rows),
    )
    return len(new_rows)


# ── GitHub fetcher ────────────────────────────────────────────────────────────
async def _fetch_github(creds: dict, tenant_id: str, integration_id: str) -> int:
    token = creds.get("access_token", "")
    org   = (creds.get("org") or "").strip()
    if not token:
        raise ValueError("GitHub access_token missing from credentials")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    records: list[RawRecord] = []

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        # Always fetch the full authenticated repo list (includes private repos).
        # /user/repos?affiliation=owner,collaborator returns everything the PAT can access.
        # If an org is specified (and is a real GitHub org, not a username), also fetch
        # that org's repos and merge them in.
        user_repos_resp = await client.get(
            "https://api.github.com/user/repos",
            params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator"},
        )
        user_repos_resp.raise_for_status()
        repos: list[dict] = user_repos_resp.json()
        logger.info("GitHub: PAT owner has access to %d repos", len(repos))

        # If org is explicitly set (and isn't an email or the user's own login), also pull
        # org repos in case the PAT doesn't have them via affiliation.
        if org and "@" not in org:
            org_resp = await client.get(
                f"https://api.github.com/orgs/{org}/repos",
                params={"per_page": 100, "sort": "updated"},
            )
            if org_resp.status_code == 200:
                existing_names = {r["full_name"] for r in repos}
                for r in org_resp.json():
                    if r["full_name"] not in existing_names:
                        repos.append(r)
                logger.info("GitHub: merged org '%s' repos, total now %d", org, len(repos))

        # Sort by most recently updated and cap to avoid extremely long syncs
        repos.sort(key=lambda r: r.get("updated_at", ""), reverse=True)

        for repo in repos[:20]:  # cap at 20 repos per sync
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
        # /rest/api/3/search/jql uses cursor-based pagination via nextPageToken
        next_page_token: str | None = None
        while True:
            body: dict = {
                "jql": "created >= -365d ORDER BY updated DESC",
                "maxResults": 50,
                "fields": ["summary", "description", "status", "assignee", "reporter",
                           "created", "updated", "issuetype", "project"],
            }
            if next_page_token:
                body["nextPageToken"] = next_page_token

            resp = await client.post(
                f"{server_url}/rest/api/3/search/jql",
                json=body,
            )
            if not resp.is_success:
                logger.error("Jira search HTTP %s — body: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
            data   = resp.json()
            issues = data.get("issues", [])
            if not issues:
                break

            def _extract_adf(node: dict) -> str:
                if node.get("type") == "text":
                    return node.get("text", "")
                return " ".join(_extract_adf(c) for c in node.get("content", []))

            for issue in issues:
                f = issue.get("fields", {})
                raw_desc = f.get("description")
                if isinstance(raw_desc, str):
                    desc = raw_desc
                elif isinstance(raw_desc, dict):
                    desc = _extract_adf(raw_desc)
                else:
                    desc = ""

                status    = (f.get("status") or {}).get("name", "Unknown")
                issue_type = (f.get("issuetype") or {}).get("name", "")
                project   = (f.get("project") or {}).get("name", "")
                assignee  = (f.get("assignee") or {}).get("displayName", "Unassigned")
                content = (
                    f"[{issue['key']}] {f.get('summary', '')}\n"
                    f"Status: {status}  Type: {issue_type}  Project: {project}  Assignee: {assignee}\n\n"
                    f"{desc}"
                ).strip()
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

            next_page_token = data.get("nextPageToken")
            if not next_page_token or len(records) >= 200:
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


# ── Elasticsearch fetcher ─────────────────────────────────────────────────────
async def _fetch_elasticsearch(creds: dict, tenant_id: str, integration_id: str, since: datetime | None = None) -> int:
    """
    Pulls ERROR/WARNING+ logs from Elasticsearch using the Search API.
    Supports both API-key auth and basic (username/password) auth.
    Uses search_after for efficient, stateless pagination.
    Only fetches logs newer than `since` (integration.last_synced_at).
    """
    url = creds.get("url", "").rstrip("/")
    api_key = creds.get("api_key", "")
    username = creds.get("username", "")
    password = creds.get("password", "")
    index = creds.get("index", "logs-*,filebeat-*,logstash-*")

    if not url:
        raise ValueError("Elasticsearch url missing from credentials")

    headers: dict = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"ApiKey {api_key}"

    auth = (username, password) if (username and password and not api_key) else None

    # Only fetch logs at WARNING level or above to keep storage lean
    level_filter = ["WARNING", "WARN", "ERROR", "CRITICAL", "FATAL", "ALERT", "EMERGENCY"]
    since_dt = since or datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0)

    query: dict = {
        "size": 500,
        "sort": [{"@timestamp": "asc"}, {"_id": "asc"}],
        "query": {
            "bool": {
                "must": [
                    {"range": {"@timestamp": {"gt": since_dt.isoformat()}}},
                    {"terms": {"log.level": level_filter}},
                ]
            }
        },
        "_source": ["@timestamp", "message", "log.level", "service.name", "host.name", "error.type", "trace.id"],
    }

    records: list[RawRecord] = []
    search_after: list | None = None
    max_pages = 20  # safety cap — 10,000 records per sync cycle

    # If the cluster uses a self-signed cert, customers can supply ca_cert (path
    # or False to disable verification). Default is True (verify against system CAs).
    ssl_verify = creds.get("ca_cert", True)
    async with httpx.AsyncClient(headers=headers, auth=auth, timeout=30, verify=ssl_verify) as client:
        for _ in range(max_pages):
            if search_after:
                query["search_after"] = search_after

            resp = await client.post(f"{url}/{index}/_search", json=query)
            if resp.status_code == 404:
                break  # index doesn't exist yet
            resp.raise_for_status()

            hits = resp.json().get("hits", {}).get("hits", [])
            if not hits:
                break

            for hit in hits:
                src = hit.get("_source", {})
                ts_raw = src.get("@timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except Exception:
                    ts = datetime.now(tz=timezone.utc)

                level = (
                    src.get("log", {}).get("level")
                    or src.get("level")
                    or "ERROR"
                ).upper()
                message = src.get("message", "").strip()
                service = src.get("service", {}).get("name", "") if isinstance(src.get("service"), dict) else src.get("service", "")

                if not message:
                    continue

                records.append(RawRecord(
                    source_type="elasticsearch",
                    source_id=hit["_id"],
                    title=f"[{level}] {service + ': ' if service else ''}{message[:120]}",
                    content=message[:8000],
                    author=service or src.get("host", {}).get("name", "") if isinstance(src.get("host"), dict) else "",
                    url="",
                    created_at=ts,
                    updated_at=ts,
                    metadata={"level": level, "service": service, "index": hit.get("_index", ""), "trace_id": src.get("trace", {}).get("id", "")},
                ))

            search_after = hits[-1].get("sort")
            if len(hits) < 500:
                break

    logger.info("Elasticsearch direct sync: %d records for tenant %s", len(records), tenant_id)
    return await _batch_save_and_embed_logs(records, tenant_id)


# ── Datadog fetcher ───────────────────────────────────────────────────────────
async def _fetch_datadog(creds: dict, tenant_id: str, integration_id: str, since: datetime | None = None) -> int:
    """
    Pulls logs from the Datadog Logs API v2.
    Only fetches ERROR/WARN+ severity. Paginates via cursor.
    """
    api_key = creds.get("api_key", "")
    app_key = creds.get("app_key", "")
    site = creds.get("site", "datadoghq.com")  # e.g. datadoghq.eu for EU customers

    if not api_key:
        raise ValueError("Datadog api_key missing from credentials")

    since_dt = since or datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0)
    now_dt = datetime.now(tz=timezone.utc)

    headers = {
        "DD-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    if app_key:
        headers["DD-APPLICATION-KEY"] = app_key

    body: dict = {
        "filter": {
            "from": since_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "query": "status:(error OR warn OR critical)",
        },
        "sort": "timestamp",
        "page": {"limit": 1000},
    }

    records: list[RawRecord] = []
    cursor: str | None = None
    max_pages = 10

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        for _ in range(max_pages):
            if cursor:
                body["page"]["cursor"] = cursor

            resp = await client.post(f"https://api.{site}/api/v2/logs/events/search", json=body)
            resp.raise_for_status()
            data = resp.json()

            for log in data.get("data", []):
                attrs = log.get("attributes", {})
                status = attrs.get("status", "error").upper()
                message = attrs.get("message", "").strip()
                service = attrs.get("service", "")
                ts_raw = attrs.get("timestamp", "")

                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except Exception:
                    ts = datetime.now(tz=timezone.utc)

                if not message:
                    continue

                records.append(RawRecord(
                    source_type="datadog",
                    source_id=log.get("id", ""),
                    title=f"[{status}] {service + ': ' if service else ''}{message[:120]}",
                    content=message[:8000],
                    author=service or attrs.get("host", ""),
                    url="",
                    created_at=ts,
                    updated_at=ts,
                    metadata={"status": status, "service": service, "host": attrs.get("host", ""), "tags": attrs.get("tags", [])},
                ))

            meta = data.get("meta", {})
            cursor = meta.get("page", {}).get("after")
            if not cursor or len(data.get("data", [])) < 1000:
                break

    logger.info("Datadog direct sync: %d records for tenant %s", len(records), tenant_id)
    return await _batch_save_and_embed_logs(records, tenant_id)


# ── CloudWatch fetcher ────────────────────────────────────────────────────────
def _fetch_cloudwatch_sync(creds: dict, since_ms: int, now_ms: int) -> list[dict]:
    """
    Pure-sync worker that runs in a thread pool executor so the boto3 blocking
    calls (describe_log_groups, filter_log_events) never stall the event loop.
    Returns raw event dicts — timestamps, messages, log group names.
    """
    import boto3  # type: ignore

    access_key = creds["aws_access_key_id"]
    secret_key = creds["aws_secret_access_key"]
    region = creds.get("region", "us-east-1")
    log_groups_raw = creds.get("log_groups", "")

    client = boto3.client(
        "logs",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    if log_groups_raw:
        log_groups = [g.strip() for g in log_groups_raw.split(",") if g.strip()]
    else:
        resp = client.describe_log_groups(limit=20)
        log_groups = [g["logGroupName"] for g in resp.get("logGroups", [])]

    filter_pattern = "?ERROR ?Exception ?CRITICAL ?FATAL ?Error ?exception"
    raw_events: list[dict] = []

    for log_group in log_groups:
        kwargs: dict = {
            "logGroupName": log_group,
            "startTime": since_ms,
            "endTime": now_ms,
            "filterPattern": filter_pattern,
            "limit": 1000,
        }
        while True:
            try:
                response = client.filter_log_events(**kwargs)
            except client.exceptions.ResourceNotFoundException:
                break
            for event in response.get("events", []):
                raw_events.append({**event, "_log_group": log_group, "_region": region})
            next_token = response.get("nextToken")
            if not next_token or len(raw_events) >= 5000:
                break
            kwargs["nextToken"] = next_token

    return raw_events


async def _fetch_cloudwatch(creds: dict, tenant_id: str, integration_id: str, since: datetime | None = None) -> int:
    """
    Polls AWS CloudWatch Logs for ERROR/WARN+ entries using boto3.
    boto3 is synchronous, so we offload the entire I/O work to a thread-pool
    executor — this keeps the asyncio event loop free while the AWS calls block.
    """
    import asyncio

    access_key = creds.get("aws_access_key_id", "")
    secret_key = creds.get("aws_secret_access_key", "")
    if not access_key or not secret_key:
        raise ValueError("CloudWatch aws_access_key_id / aws_secret_access_key missing from credentials")

    since_dt = since or datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0)
    since_ms = int(since_dt.timestamp() * 1000)
    now_ms   = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    # Run all blocking boto3 calls in the default thread-pool executor
    loop = asyncio.get_event_loop()
    raw_events: list[dict] = await loop.run_in_executor(
        None, _fetch_cloudwatch_sync, creds, since_ms, now_ms
    )

    records: list[RawRecord] = []
    for event in raw_events:
        message = event.get("message", "").strip()
        if not message:
            continue
        ts = datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)
        log_group = event["_log_group"]
        region    = event["_region"]
        source_id = event.get("eventId", f"{log_group}:{event['timestamp']}")
        records.append(RawRecord(
            source_type="cloudwatch",
            source_id=source_id,
            title=f"[ERROR] {log_group}: {message[:100]}",
            content=message[:8000],
            author=log_group,
            url="",
            created_at=ts,
            updated_at=ts,
            metadata={"log_group": log_group, "log_stream": event.get("logStreamName", ""), "region": region},
        ))

    logger.info("CloudWatch direct sync: %d records for tenant %s", len(records), tenant_id)
    return await _batch_save_and_embed_logs(records, tenant_id)


# ── GCP Logging fetcher ───────────────────────────────────────────────────────
async def _fetch_gcp_logging(creds: dict, tenant_id: str, integration_id: str, since: datetime | None = None) -> int:
    """
    Pulls logs from Google Cloud Logging REST API using a service account JSON key.
    Filters for severity >= WARNING. Paginates via pageToken.
    """
    import json as _json
    import google.auth  # type: ignore
    import google.auth.transport.requests  # type: ignore
    from google.oauth2 import service_account  # type: ignore

    sa_json_raw = creds.get("service_account_json", "")
    project_id = creds.get("project_id", "")

    if not sa_json_raw:
        raise ValueError("GCP service_account_json missing from credentials")

    sa_info = _json.loads(sa_json_raw)
    if not project_id:
        project_id = sa_info.get("project_id", "")

    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/logging.read"]
    )

    # credentials.refresh() uses the synchronous `requests` library under the
    # hood, which would block the event loop.  Run it in a thread-pool executor.
    import asyncio as _asyncio

    def _refresh_token() -> str:
        auth_req = google.auth.transport.requests.Request()
        credentials.refresh(auth_req)
        return credentials.token

    loop = _asyncio.get_event_loop()
    token: str = await loop.run_in_executor(None, _refresh_token)

    since_dt = since or datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0)
    since_rfc = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    filter_str = (
        f'resource.type!="" AND severity>=WARNING AND timestamp>="{since_rfc}"'
    )

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    records: list[RawRecord] = []
    page_token: str | None = None
    max_pages = 10

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        for _ in range(max_pages):
            body: dict = {
                "resourceNames": [f"projects/{project_id}"],
                "filter": filter_str,
                "orderBy": "timestamp asc",
                "pageSize": 1000,
            }
            if page_token:
                body["pageToken"] = page_token

            resp = await client.post("https://logging.googleapis.com/v2/entries:list", json=body)
            resp.raise_for_status()
            data = resp.json()

            for entry in data.get("entries", []):
                ts_raw = entry.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except Exception:
                    ts = datetime.now(tz=timezone.utc)

                severity = entry.get("severity", "DEFAULT")
                payload = entry.get("textPayload") or str(entry.get("jsonPayload", "")) or str(entry.get("protoPayload", ""))
                resource = entry.get("resource", {})
                service = resource.get("labels", {}).get("service_name", resource.get("type", ""))
                log_name = entry.get("logName", "")

                if not payload:
                    continue

                records.append(RawRecord(
                    source_type="gcp",
                    source_id=entry.get("insertId", f"{log_name}:{ts_raw}"),
                    title=f"[{severity}] {service + ': ' if service else ''}{payload[:120]}",
                    content=payload[:8000],
                    author=service,
                    url="",
                    created_at=ts,
                    updated_at=ts,
                    metadata={"severity": severity, "service": service, "log_name": log_name, "project_id": project_id},
                ))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

    logger.info("GCP Logging direct sync: %d records for tenant %s", len(records), tenant_id)
    return await _batch_save_and_embed_logs(records, tenant_id)


# ── Splunk fetcher ────────────────────────────────────────────────────────────
async def _fetch_splunk(creds: dict, tenant_id: str, integration_id: str, since: datetime | None = None) -> int:
    """
    Pulls logs from Splunk via the REST Search API (POST /services/search/jobs/export).
    Authenticates with a Splunk auth token.
    """
    url = creds.get("url", "").rstrip("/")
    token = creds.get("auth_token", "")
    index = creds.get("index", "main")

    if not url or not token:
        raise ValueError("Splunk url and auth_token required in credentials")

    since_dt = since or datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0)
    earliest = since_dt.strftime("%m/%d/%Y:%H:%M:%S")

    spl = (
        f'search index={index} (log_level=ERROR OR log_level=WARN OR log_level=CRITICAL '
        f'OR level=ERROR OR level=WARN OR severity=ERROR OR severity=WARN) '
        f'earliest="{earliest}" | head 2000 | fields _time, log_level, level, severity, message, host, source'
    )

    headers = {"Authorization": f"Splunk {token}"}
    records: list[RawRecord] = []

    # Splunk on-prem often uses self-signed certs. Customers can supply
    # ca_cert (path) or False to disable. Default is True (system CAs).
    ssl_verify = creds.get("ca_cert", True)
    async with httpx.AsyncClient(headers=headers, timeout=60, verify=ssl_verify) as client:
        resp = await client.post(
            f"{url}/services/search/jobs/export",
            data={"search": spl, "output_mode": "json", "count": 0},
        )
        if resp.status_code == 401:
            raise ValueError("Splunk authentication failed — check auth_token")
        resp.raise_for_status()

        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                import json as _json
                obj = _json.loads(line)
            except Exception:
                continue

            result = obj.get("result", {})
            message = result.get("message", "").strip()
            if not message:
                continue

            level = result.get("log_level") or result.get("level") or result.get("severity") or "ERROR"
            ts_raw = result.get("_time", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except Exception:
                ts = datetime.now(tz=timezone.utc)

            records.append(RawRecord(
                source_type="splunk",
                source_id=f"{index}:{ts_raw}:{hashlib.sha256(message.encode()).hexdigest()[:16]}",
                title=f"[{level.upper()}] {result.get('host', '')}: {message[:100]}",
                content=message[:8000],
                author=result.get("host", ""),
                url="",
                created_at=ts,
                updated_at=ts,
                metadata={"level": level, "host": result.get("host", ""), "source": result.get("source", ""), "index": index},
            ))

    logger.info("Splunk direct sync: %d records for tenant %s", len(records), tenant_id)
    return await _batch_save_and_embed_logs(records, tenant_id)


# ── Azure Monitor fetcher ─────────────────────────────────────────────────────
async def _fetch_azure_monitor(creds: dict, tenant_id: str, integration_id: str, since: datetime | None = None) -> int:
    """
    Pulls logs from Azure Monitor Log Analytics workspace using the REST Query API.
    Authenticates via client credentials (service principal).
    """
    azure_tenant = creds.get("azure_tenant_id", "")
    client_id = creds.get("client_id", "")
    client_secret = creds.get("client_secret", "")
    workspace_id = creds.get("workspace_id", "")
    table = creds.get("table", "AzureDiagnostics")

    if not all([azure_tenant, client_id, client_secret, workspace_id]):
        raise ValueError("Azure credentials require: azure_tenant_id, client_id, client_secret, workspace_id")

    # Get access token via client credentials grant
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            f"https://login.microsoftonline.com/{azure_tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://api.loganalytics.io/.default",
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

    since_dt = since or datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # KQL query — filter for errors only
    kql = (
        f"{table} "
        f'| where TimeGenerated >= datetime("{since_iso}") '
        f"| where Level in ('Error', 'Warning', 'Critical') or SeverityLevel in ('error', 'warning', 'critical') "
        f"| project TimeGenerated, Level, Message, ResourceId, OperationName, Category "
        f"| order by TimeGenerated asc "
        f"| take 2000"
    )

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    records: list[RawRecord] = []

    async with httpx.AsyncClient(headers=headers, timeout=60) as client:
        resp = await client.post(
            f"https://api.loganalytics.io/v1/workspaces/{workspace_id}/query",
            json={"query": kql},
        )
        resp.raise_for_status()
        data = resp.json()

        tables = data.get("tables", [])
        if not tables:
            return 0

        tbl = tables[0]
        cols = [c["name"] for c in tbl.get("columns", [])]

        for row in tbl.get("rows", []):
            entry = dict(zip(cols, row))
            message = entry.get("Message", "").strip()
            if not message:
                continue

            ts_raw = entry.get("TimeGenerated", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except Exception:
                ts = datetime.now(tz=timezone.utc)

            level = entry.get("Level") or entry.get("SeverityLevel") or "Error"
            resource = entry.get("ResourceId", "")

            records.append(RawRecord(
                source_type="azuremonitor",
                source_id=f"{workspace_id}:{ts_raw}:{hashlib.sha256(message.encode()).hexdigest()[:16]}",
                title=f"[{level}] {entry.get('OperationName', resource)[:80]}: {message[:80]}",
                content=message[:8000],
                author=resource,
                url="",
                created_at=ts,
                updated_at=ts,
                metadata={"level": level, "resource_id": resource, "operation": entry.get("OperationName", ""), "category": entry.get("Category", ""), "table": table},
            ))

    logger.info("Azure Monitor direct sync: %d records for tenant %s", len(records), tenant_id)
    return await _batch_save_and_embed_logs(records, tenant_id)


# ── Zendesk ──────────────────────────────────────────────────────────────────
async def _fetch_zendesk(creds: dict, tenant_id: str, integration_id: str) -> int:
    """
    Fetch recent Zendesk tickets and comments.
    Creds: subdomain, email, api_token
    """
    subdomain = creds.get("subdomain", "").strip().rstrip(".zendesk.com")
    email     = creds.get("email", "").strip()
    api_token = creds.get("api_token", "").strip()

    if not subdomain or not email or not api_token:
        raise ValueError("Zendesk credentials missing: subdomain, email, and api_token are required.")

    base_url = f"https://{subdomain}.zendesk.com/api/v2"
    auth     = (f"{email}/token", api_token)
    records: list[RawRecord] = []

    async with httpx.AsyncClient(timeout=30) as client:
        url: str | None = f"{base_url}/tickets.json?sort_by=created_at&sort_order=desc&per_page=100"
        pages = 0
        while url and pages < 5:
            resp = await client.get(url, auth=auth)
            if resp.status_code == 401:
                raise ValueError("Zendesk authentication failed — check email and API token.")
            resp.raise_for_status()
            data = resp.json()
            for ticket in data.get("tickets", []):
                tid_str   = str(ticket.get("id", ""))
                subject   = ticket.get("subject") or f"Ticket #{tid_str}"
                body      = ticket.get("description") or ""
                status_   = ticket.get("status", "")
                priority  = ticket.get("priority") or "normal"
                requester = str(ticket.get("requester_id", ""))
                created   = _parse_dt(ticket.get("created_at"))
                updated   = _parse_dt(ticket.get("updated_at"))

                records.append(RawRecord(
                    source_type="zendesk",
                    source_id=f"ticket-{tid_str}",
                    title=f"[{priority.upper()}] {subject}",
                    content=f"Status: {status_}\n\n{body}"[:8000],
                    author=requester,
                    url=f"https://{subdomain}.zendesk.com/agent/tickets/{tid_str}",
                    created_at=created,
                    updated_at=updated,
                    metadata={"ticket_id": tid_str, "status": status_, "priority": priority},
                ))
            url   = data.get("next_page")
            pages += 1

    logger.info("Zendesk direct sync: %d tickets for tenant %s", len(records), tenant_id)
    for rec in records:
        try:
            await _save_and_embed(rec, tenant_id)
        except Exception as exc:
            logger.warning("Skipping Zendesk record %s: %s", rec.source_id, exc)
    return len(records)


def _parse_dt(value: str | None) -> datetime:
    """Parse ISO 8601 string to datetime, defaulting to now on failure."""
    if not value:
        return datetime.now(tz=timezone.utc)
    try:
        from datetime import datetime as _dt
        return _dt.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(tz=timezone.utc)


# ── Google Drive ──────────────────────────────────────────────────────────────
async def _fetch_google_drive(creds: dict, tenant_id: str, integration_id: str) -> int:
    """
    Fetch Google Drive documents using a Service Account.
    Creds: service_account_json (full JSON string)

    Fetches the 200 most-recently-modified Docs/Sheets/Slides, exports them as
    plain text and stores them for RAG.
    """
    import json as _json

    sa_json_raw = creds.get("service_account_json", "").strip()
    if not sa_json_raw:
        raise ValueError("Google Drive credentials missing: service_account_json is required.")

    try:
        sa_info = _json.loads(sa_json_raw)
    except Exception:
        raise ValueError("service_account_json is not valid JSON.")

    # Use google-auth + httpx to call Drive API with a service account
    try:
        import google.oauth2.service_account as _sa_mod
        import google.auth.transport.requests as _ga_req
        creds_obj = _sa_mod.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        # Refresh to get an access token
        request = _ga_req.Request()
        creds_obj.refresh(request)
        access_token = creds_obj.token
    except ImportError:
        raise RuntimeError(
            "google-auth package not installed. "
            "Add 'google-auth' to requirements.txt to enable Google Drive sync."
        )

    records: list[RawRecord] = []
    mime_export: dict[str, str] = {
        "application/vnd.google-apps.document":     "text/plain",
        "application/vnd.google-apps.spreadsheet":  "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }

    async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {access_token}"}) as client:
        # List recent files
        list_resp = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            params={
                "pageSize": 200,
                "orderBy": "modifiedTime desc",
                "q": "trashed = false",
                "fields": "files(id,name,mimeType,modifiedTime,createdTime,webViewLink,owners)",
            },
        )
        if list_resp.status_code == 401:
            raise ValueError("Google Drive authentication failed — check service account JSON.")
        list_resp.raise_for_status()

        for file in list_resp.json().get("files", []):
            file_id   = file["id"]
            name      = file.get("name", "Untitled")
            mime_type = file.get("mimeType", "")
            export_mime = mime_export.get(mime_type)

            if not export_mime:
                continue  # skip non-text-exportable files (PDFs, images, etc.)

            try:
                export_resp = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                    params={"mimeType": export_mime},
                    timeout=30,
                )
                if not export_resp.is_success:
                    continue
                content = export_resp.text[:8000]
            except Exception:
                continue

            owner  = (file.get("owners") or [{}])[0].get("emailAddress", "")
            created = _parse_dt(file.get("createdTime"))
            updated = _parse_dt(file.get("modifiedTime"))

            records.append(RawRecord(
                source_type="google_drive",
                source_id=file_id,
                title=name,
                content=content,
                author=owner,
                url=file.get("webViewLink", ""),
                created_at=created,
                updated_at=updated,
                metadata={"mime_type": mime_type, "file_id": file_id},
            ))

    logger.info("Google Drive direct sync: %d files for tenant %s", len(records), tenant_id)
    for rec in records:
        try:
            await _save_and_embed(rec, tenant_id)
        except Exception as exc:
            logger.warning("Skipping Google Drive record %s: %s", rec.source_id, exc)
    return len(records)


# ── Dispatch ──────────────────────────────────────────────────────────────────
_FETCHERS = {
    "github":        _fetch_github,
    "jira":          _fetch_jira,
    "slack":         _fetch_slack,
    "hubspot":       _fetch_hubspot,
    "zendesk":       _fetch_zendesk,
    "google_drive":  _fetch_google_drive,
    # Log sources — polled directly, zero config required from customer
    "elasticsearch": _fetch_elasticsearch,
    "datadog":       _fetch_datadog,
    "cloudwatch":    _fetch_cloudwatch,
    # Accept both the short key (legacy) and the canonical UI key
    "gcp":           _fetch_gcp_logging,
    "gcp_logging":   _fetch_gcp_logging,
    "splunk":        _fetch_splunk,
    "azuremonitor":  _fetch_azure_monitor,
    "azure_monitor": _fetch_azure_monitor,
}

# Which source types are log sources (polled periodically vs. contextual one-time syncs)
LOG_SOURCE_TYPES = frozenset({
    "elasticsearch", "datadog", "cloudwatch",
    "gcp", "gcp_logging",
    "splunk",
    "azuremonitor", "azure_monitor",
})


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

        # Capture last_synced_at for incremental log source fetches.
        # For log sources this becomes the `since` parameter so we only
        # pull new records, not re-process everything on every run.
        last_synced_at = integration.last_synced_at

        integration.status = "pending"
        await db.commit()

    # Run the fetch (outside the session to avoid long-held connections)
    try:
        # Log source fetchers accept a `since` kwarg for incremental sync.
        # Contextual fetchers (github, jira, slack) ignore it safely.
        since = last_synced_at if source_type in LOG_SOURCE_TYPES else None
        count = await fetcher(raw_creds, str(tenant_id), integration_id, since=since)
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
