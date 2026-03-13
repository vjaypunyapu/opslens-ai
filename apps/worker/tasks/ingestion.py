"""
OpsLens AI – Document Processing Pipeline
Normalise → Deduplicate → Chunk → Embed → Upsert to Qdrant
"""
from __future__ import annotations

import hashlib
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


NORMALIZERS = {
    "slack":  SlackNormalizer,
    "jira":   JiraNormalizer,
    "gdrive": GDriveNormalizer,
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
    """Embed all chunks and upsert to Qdrant in batches."""
    collection_name = f"opslens_{tenant_id}"

    # Ensure collection exists
    existing = [c.name for c in _qdrant.get_collections().collections]
    if collection_name not in existing:
        _qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=EMBED_DIMS, distance=Distance.COSINE),
        )

    points: list[PointStruct] = []

    for batch_start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[batch_start: batch_start + EMBED_BATCH]
        response = await _openai.embeddings.create(model=EMBED_MODEL, input=batch)

        for local_i, emb_obj in enumerate(response.data):
            chunk_idx = batch_start + local_i
            points.append(
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

    if points:
        _qdrant.upsert(collection_name=collection_name, points=points, wait=True)
        logger.info("Upserted %d points to %s", len(points), collection_name)


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
    normalizer_cls = NORMALIZERS.get(source_type)
    if not normalizer_cls:
        return {"error": f"No normalizer for source_type={source_type}"}

    # In a real implementation, query airbyte_staging.<source_type>_<stream>
    # filtered by _airbyte_loaded_at > last_processed timestamp
    # This is a simplified skeleton:
    processed = 0
    async with AsyncSession() as db:
        # ... query staging table, normalise, upsert to canonical_documents ...
        # For each new/updated record:
        #   1. normalise(raw) → RawRecord
        #   2. compute content_hash
        #   3. upsert to canonical_documents (ON CONFLICT DO NOTHING if hash unchanged)
        #   4. enqueue process_document.delay(doc_id, tenant_id)
        pass

    return {"tenant_id": tenant_id, "source_type": source_type, "processed": processed}
