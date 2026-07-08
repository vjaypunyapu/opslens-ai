"""
OpsLens AI — Hybrid Retriever
==============================
Combines dense (embedding) search from Qdrant with BM25 sparse search,
fuses the results using Reciprocal Rank Fusion (RRF), then re-ranks the
merged candidate set with Cohere Rerank for maximum precision.

Pipeline:
  query
    ├─► Qdrant dense search (top-K by cosine similarity)
    └─► BM25 keyword search over the same tenant's corpus (top-K)
          └─► RRF merge   → top-(2*K) candidates
                 └─► Cohere Rerank  → top-K final docs
                           └─► returned to caller

BM25 index is built lazily from CanonicalDocument.content rows pulled from
Postgres for the tenant.  Results are LangChain Document objects so the
existing rag_service chain needs zero changes to its output handling.

Cost / latency notes:
  - Cohere Rerank: ~2 ms per call, $0.001 / 1 000 docs.  Skipped gracefully
    if COHERE_API_KEY is unset.
  - BM25 index build: ~30 ms per 1 000 docs (done in-process, no extra infra).
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
from langchain_core.documents import Document

from ..config import settings
from ..utils.logging import get_logger
from . import telemetry
from .query_intent import QueryIntent, detect as detect_intent

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
TOP_K         = settings.QDRANT_TOP_K   # e.g. 10
RRF_K         = 60                      # RRF constant (standard = 60)
COHERE_MODEL  = "rerank-english-v3.0"

# Keywords that signal the user wants the most recent document, not just the
# most semantically relevant one.  When any of these appear in the query we
# re-sort the final candidate set by source_created_at so the LLM sees the
# newest items first and can correctly answer "last / latest / most recent X".
_TEMPORAL_KEYWORDS = frozenset([
    "last", "latest", "most recent", "newest", "recent",
    "just", "today", "yesterday", "this week", "this month",
])

def _is_temporal_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _TEMPORAL_KEYWORDS)

# ── API key helpers ────────────────────────────────────────────────────────────
def _openai_key() -> str:
    return (
        os.environ.get("OPENAI_TOKEN", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or settings.OPENAI_API_KEY
        or ""
    ).strip()

def _qdrant_key() -> str | None:
    k = (
        os.environ.get("QDRANT_TOKEN", "")
        or os.environ.get("QDRANT_API_KEY", "")
        or (settings.QDRANT_API_KEY or "")
    ).strip()
    return k or None

def _cohere_key() -> str | None:
    return (os.environ.get("COHERE_API_KEY", "") or "").strip() or None

def _qdrant_base() -> str:
    return settings.QDRANT_URL.rstrip("/")

def _qdrant_headers() -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    k = _qdrant_key()
    if k:
        h["api-key"] = k
    return h


# ── Dense search (Qdrant REST) ────────────────────────────────────────────────
async def _dense_search(
    query_vector: list[float],
    collection: str,
    top_k: int,
    allowed_sources: list[dict] | None = None,
) -> list[Document]:
    """Hit Qdrant's /points/search endpoint directly via httpx.

    allowed_sources: list of {source_type, source_id} dicts. When provided,
    adds a Qdrant filter so only documents from those sources are returned.
    None means no filtering (admin / unrestricted user).
    """
    base = _qdrant_base()
    payload: dict = {
        "vector": query_vector,
        "limit": top_k,
        "with_payload": True,
        "with_vector": False,
    }

    # Build source_id filter when the user is restricted
    if allowed_sources is not None:
        if not allowed_sources:
            # User has no permitted sources — return nothing immediately
            return []
        source_ids = list({s["source_id"] for s in allowed_sources})
        payload["filter"] = {
            "must": [
                {
                    "key": "metadata.source_id",
                    "match": {"any": source_ids},
                }
            ]
        }

    async with httpx.AsyncClient(headers=_qdrant_headers(), timeout=30) as client:
        resp = await client.post(f"{base}/collections/{collection}/points/search", json=payload)
        if resp.status_code == 404:
            logger.warning("dense_search: collection %s not found", collection)
            return []
        resp.raise_for_status()

    results = resp.json().get("result", [])
    docs: list[Document] = []
    for r in results:
        payload_data = r.get("payload", {})
        meta = payload_data.get("metadata", {})
        docs.append(Document(
            page_content=payload_data.get("page_content", ""),
            metadata={**meta, "_qdrant_id": r.get("id"), "_score": r.get("score", 0.0)},
        ))
    return docs


# ── Full-text search (PostgreSQL tsvector) ────────────────────────────────────
async def _bm25_search(
    query: str,
    tenant_id: str,
    top_k: int,
    allowed_sources: list[dict] | None = None,
) -> list[Document]:
    """
    Keyword search using PostgreSQL full-text search (tsvector + GIN index).

    Replaces the previous in-memory BM25 approach which loaded all documents
    into memory per-query and did not scale past ~10k documents per tenant.

    PostgreSQL FTS uses the pre-built GIN index on the search_vector column,
    making it O(log N) rather than O(N). ts_rank_cd provides BM25-style scoring.

    Falls back to in-memory BM25 (rank_bm25) if the search_vector column is
    not yet populated (e.g. on the first deploy before backfill completes).

    allowed_sources: list of {source_type, source_id} dicts. When provided,
    the SQL query is filtered to only include matching documents.
    None means no filtering (admin / unrestricted user).
    """
    if allowed_sources is not None and not allowed_sources:
        return []   # user has no permitted sources

    import sqlalchemy as sa
    from ..db.session import AsyncSessionFactory as async_session_factory

    # Build allowed source_ids filter clause
    source_filter = ""
    params: dict = {"tenant_id": tenant_id, "query_text": query, "top_k": top_k}
    if allowed_sources is not None:
        allowed_ids = [s["source_id"] for s in allowed_sources]
        source_filter = "AND source_id = ANY(:allowed_ids)"
        params["allowed_ids"] = allowed_ids

    try:
        async with async_session_factory() as db:
            rows = (await db.execute(sa.text(f"""
                SELECT
                    id, content, title, url, source_type, source_id,
                    author, source_created_at,
                    ts_rank_cd(search_vector, query) AS rank
                FROM opslens.canonical_documents,
                     plainto_tsquery('english', :query_text) query
                WHERE tenant_id = :tenant_id::uuid
                  AND search_vector IS NOT NULL
                  AND search_vector @@ query
                  {source_filter}
                ORDER BY rank DESC
                LIMIT :top_k
            """), params)).fetchall()

        if rows:
            return [
                Document(
                    page_content=row.content,
                    metadata={
                        "document_id": str(row.id),
                        "source_type": row.source_type,
                        "title":       row.title or "",
                        "url":         row.url or "",
                        "author":      row.author or "",
                        "created_at":  row.source_created_at.isoformat() if row.source_created_at else None,
                        "_bm25_score": float(row.rank),
                    },
                )
                for row in rows
            ]

        # search_vector not yet populated — fall back to in-memory BM25
        logger.info("search_vector empty — falling back to in-memory BM25")

    except Exception as fts_exc:
        logger.warning("PostgreSQL FTS failed (%s) — falling back to in-memory BM25", fts_exc)

    # ── In-memory BM25 fallback (removed once tsvector backfill is complete) ──
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning("rank_bm25 not installed and tsvector unavailable — keyword search disabled")
        return []

    from ..db.models import CanonicalDocument
    async with async_session_factory() as db:
        q = (
            sa.select(CanonicalDocument.id, CanonicalDocument.content, CanonicalDocument.title,
                      CanonicalDocument.url, CanonicalDocument.source_type, CanonicalDocument.source_id,
                      CanonicalDocument.author, CanonicalDocument.source_created_at)
            .where(CanonicalDocument.tenant_id == uuid.UUID(tenant_id))
        )
        if allowed_sources is not None:
            allowed_ids = [s["source_id"] for s in allowed_sources]
            q = q.where(CanonicalDocument.source_id.in_(allowed_ids))
        q = q.limit(5000)
        result = await db.execute(q)
        rows = result.fetchall()

    if not rows:
        return []

    tokenised = [row.content.lower().split() for row in rows]
    bm25 = BM25Okapi(tokenised)
    scores = bm25.get_scores(query.lower().split())
    top_indices = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]

    return [
        Document(
            page_content=rows[idx].content,
            metadata={
                "document_id": str(rows[idx].id),
                "source_type": rows[idx].source_type,
                "title":       rows[idx].title or "",
                "url":         rows[idx].url or "",
                "author":      rows[idx].author or "",
                "created_at":  rows[idx].source_created_at.isoformat() if rows[idx].source_created_at else None,
                "_bm25_score": float(scores[idx]),
            },
        )
        for idx in top_indices
        if scores[idx] > 0
    ]


# ── Reciprocal Rank Fusion ─────────────────────────────────────────────────────
def _rrf_merge(
    dense_docs: list[Document],
    bm25_docs: list[Document],
    k: int = RRF_K,
) -> list[Document]:
    """
    Fuse two ranked lists using Reciprocal Rank Fusion.
    score(d) = Σ 1 / (k + rank_i(d))
    De-duplicates by page_content (first 200 chars as key).
    """
    scores: dict[str, float]   = {}
    doc_map: dict[str, Document] = {}

    def _key(doc: Document) -> str:
        return doc.page_content[:200]

    for rank, doc in enumerate(dense_docs, start=1):
        key = _key(doc)
        scores[key]  = scores.get(key, 0.0) + 1.0 / (k + rank)
        doc_map[key] = doc

    for rank, doc in enumerate(bm25_docs, start=1):
        key = _key(doc)
        scores[key]  = scores.get(key, 0.0) + 1.0 / (k + rank)
        if key not in doc_map:
            doc_map[key] = doc

    merged = sorted(doc_map.keys(), key=lambda k: -scores[k])
    result: list[Document] = []
    for key in merged:
        d = doc_map[key]
        d.metadata["_rrf_score"] = scores[key]
        result.append(d)
    return result


# ── Cohere Reranker ────────────────────────────────────────────────────────────
async def _cohere_rerank(
    query: str,
    docs: list[Document],
    top_k: int,
) -> list[Document]:
    """
    Rerank docs using Cohere's cross-encoder rerank endpoint.
    Falls back to returning docs as-is if key is missing or call fails.
    """
    api_key = _cohere_key()
    if not api_key or not docs:
        return docs[:top_k]

    try:
        import cohere
        co = cohere.AsyncClient(api_key)
        response = await co.rerank(
            model=COHERE_MODEL,
            query=query,
            documents=[d.page_content[:512] for d in docs],
            top_n=top_k,
        )
        reranked: list[Document] = []
        for r in response.results:
            d = docs[r.index]
            d.metadata["_cohere_relevance"] = r.relevance_score
            reranked.append(d)
        logger.info("cohere_rerank: %d→%d docs", len(docs), len(reranked))
        return reranked
    except Exception as exc:
        logger.warning("cohere_rerank failed (%s), returning RRF order", exc)
        return docs[:top_k]


# ── Embed query ───────────────────────────────────────────────────────────────
async def _id_lookup(id_string: str, tenant_id: str) -> list[Document]:
    """Return the single document that matches an exact identifier.

    Tries source_id prefix/suffix match first, then falls back to title ILIKE.
    Bypasses vector search entirely so the right document is always returned.
    """
    import sqlalchemy as sa
    from ..db.models import CanonicalDocument
    from ..db.session import AsyncSessionFactory as async_session_factory

    async with async_session_factory() as db:
        # Match strategy depends on ID format:
        #   Jira-style (PROJ-45)  → exact source_id match or title match
        #   GitHub numeric (#3)   → title only ("source_id" is GitHub's global int, not the PR#)
        #   Commit SHA            → source_id suffix match (stored as "commit:repo:abc123")
        import re as _re
        is_jira    = bool(_re.match(r'^[A-Z]{2,10}-\d+$', id_string))
        is_commit  = bool(_re.match(r'^[0-9a-f]{7,40}$', id_string, _re.IGNORECASE))
        is_numeric = id_string.startswith("#")

        if is_jira:
            where = sa.or_(
                CanonicalDocument.source_id.ilike(f"%{id_string}%"),
                CanonicalDocument.title.ilike(f"%{id_string}%"),
            )
        elif is_commit:
            where = sa.or_(
                CanonicalDocument.source_id.ilike(f"%{id_string}%"),
                CanonicalDocument.title.ilike(f"%{id_string}%"),
            )
        else:
            # Numeric GitHub ID — search title only to avoid matching
            # unrelated source_ids that happen to contain the digit sequence.
            where = CanonicalDocument.title.ilike(f"%{id_string}%")

        q = (
            sa.select(
                CanonicalDocument.id, CanonicalDocument.content,
                CanonicalDocument.title, CanonicalDocument.url,
                CanonicalDocument.source_type, CanonicalDocument.source_id,
                CanonicalDocument.author, CanonicalDocument.source_created_at,
                CanonicalDocument.doc_metadata,
            )
            .where(CanonicalDocument.tenant_id == uuid.UUID(tenant_id))
            .where(where)
            .limit(3)
        )
        rows = (await db.execute(q)).fetchall()

    docs: list[Document] = []
    for row in rows:
        docs.append(Document(
            page_content=row.content,
            metadata={
                "document_id": str(row.id),
                "source_type": row.source_type,
                "title":       row.title or "",
                "url":         row.url or "",
                "author":      row.author or "",
                "created_at":  row.source_created_at.isoformat() if row.source_created_at else None,
                "_intent":     "id_lookup",
            },
        ))
    logger.info("id_lookup: id=%r tenant=%s → %d docs", id_string, tenant_id, len(docs))
    return docs


async def _resolve_filter_source_ids(
    tenant_id: str,
    filters: dict[str, str],
    allowed_sources: list[dict] | None,
) -> list[dict] | None:
    """Translate intent filters (state, author, since) into a source_id allow-list.

    Returns None when no filtering is needed (pass-through), an empty list when
    no documents match (dead-end), or a list of {source_id} dicts that gets
    intersected with the user's existing allowed_sources permission set.
    """
    if not filters:
        return allowed_sources  # nothing to resolve

    import sqlalchemy as sa
    from ..db.models import CanonicalDocument
    from ..db.session import AsyncSessionFactory as async_session_factory

    async with async_session_factory() as db:
        q = sa.select(CanonicalDocument.source_id).where(
            CanonicalDocument.tenant_id == uuid.UUID(tenant_id)
        )
        if "state" in filters:
            q = q.where(
                CanonicalDocument.doc_metadata["state"].astext == filters["state"]
            )
        if "author" in filters:
            q = q.where(
                sa.or_(
                    CanonicalDocument.author.ilike(f"%{filters['author']}%"),
                    CanonicalDocument.doc_metadata["author"].astext.ilike(
                        f"%{filters['author']}%"
                    ),
                )
            )
        if "source_type" in filters:
            q = q.where(CanonicalDocument.source_type == filters["source_type"])
        if "since" in filters:
            from datetime import datetime
            since_dt = datetime.fromisoformat(filters["since"])
            q = q.where(CanonicalDocument.source_created_at >= since_dt)
        q = q.limit(2000)
        rows = (await db.execute(q)).scalars().all()

    if not rows:
        logger.info("resolve_filter_source_ids: no docs match filters %s", filters)
        return []  # signal: nothing matches

    filter_ids = {r for r in rows}

    # Intersect with the user's existing permission set when restricted
    if allowed_sources is not None:
        permitted_ids = {s["source_id"] for s in allowed_sources}
        intersected = filter_ids & permitted_ids
        return [{"source_id": sid} for sid in intersected]

    return [{"source_id": sid} for sid in filter_ids]


async def _aggregate_count(
    query: str,
    tenant_id: str,
    filters: dict[str, str],
    allowed_sources: list[dict] | None = None,
) -> Document:
    """Run a SQL COUNT(*) with any detected filters and return a synthetic Document
    containing the number so the LLM can answer count questions accurately.

    allowed_sources is respected so a source-restricted user only sees counts
    for documents they are permitted to read (same guarantee as _bm25_search).
    """
    import sqlalchemy as sa
    from ..db.models import CanonicalDocument
    from ..db.session import AsyncSessionFactory as async_session_factory

    async with async_session_factory() as db:
        q = sa.select(sa.func.count()).select_from(CanonicalDocument).where(
            CanonicalDocument.tenant_id == uuid.UUID(tenant_id)
        )
        # Enforce RBAC source restriction — same logic as _bm25_search
        if allowed_sources is not None:
            permitted_ids = [s["source_id"] for s in allowed_sources]
            if not permitted_ids:
                # User has no permitted sources — count is always zero for them
                return Document(
                    page_content="Aggregate count result: 0 documents (no accessible sources).",
                    metadata={"_intent": "aggregate", "count": 0, "filters": filters},
                )
            q = q.where(CanonicalDocument.source_id.in_(permitted_ids))
        if "state" in filters:
            q = q.where(
                CanonicalDocument.doc_metadata["state"].astext == filters["state"]
            )
        if "author" in filters:
            q = q.where(CanonicalDocument.author.ilike(f"%{filters['author']}%"))
        if "source_type" in filters:
            q = q.where(CanonicalDocument.source_type == filters["source_type"])
        if "since" in filters:
            from datetime import datetime
            since_dt = datetime.fromisoformat(filters["since"])
            q = q.where(CanonicalDocument.source_created_at >= since_dt)
        total = (await db.execute(q)).scalar() or 0

    filter_desc = ", ".join(f"{k}={v}" for k, v in filters.items())
    label = f"filtered by {filter_desc}" if filter_desc else "across all sources"
    content = f"Aggregate count result: {total} documents ({label})."
    logger.info("aggregate_count: %d docs %s for tenant=%s", total, label, tenant_id)
    return Document(
        page_content=content,
        metadata={"_intent": "aggregate", "count": total, "filters": filters},
    )


async def _embed_query(query: str) -> list[float]:
    if settings.LLM_PROVIDER == "ollama":
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.OLLAMA_URL}/api/embed",
                json={"model": settings.OLLAMA_EMBED_MODEL, "input": [query]},
            )
            resp.raise_for_status()
            return resp.json()["embeddings"][0]
    else:
        from openai import AsyncOpenAI
        oai = AsyncOpenAI(api_key=_openai_key())
        resp = await oai.embeddings.create(model=settings.OPENAI_EMBED_MODEL, input=[query])
        return resp.data[0].embedding


# ── Public API ────────────────────────────────────────────────────────────────
async def hybrid_retrieve(
    query: str,
    tenant_id: str,
    top_k: int | None = None,
    allowed_sources: list[dict] | None = None,
) -> list[Document]:
    """
    Full hybrid retrieval pipeline with intent-aware routing:
      - ID lookup queries  → direct DB match, skips vector search
      - Count/aggregate    → SQL COUNT(*) + semantic docs for context
      - Status/author/date → filter candidate set before ranking
      - All others         → dense + BM25 → RRF → Cohere rerank

    allowed_sources: list of {source_type, source_id} from permissions.get_allowed_sources().
        None  → unrestricted (admin): searches all tenant documents.
        []    → empty (no team membership): returns nothing immediately.
        [...]  → restricted: only searches within listed source_ids.
    """
    if allowed_sources is not None and not allowed_sources:
        logger.info("hybrid_retrieve: user has no allowed sources — returning empty")
        return []

    k = top_k or TOP_K
    collection = f"{settings.QDRANT_COLLECTION_PREFIX}{tenant_id}"

    # ── Intent detection ──────────────────────────────────────────────────────
    intent: QueryIntent = detect_intent(query)
    logger.info(
        "hybrid_retrieve: tenant=%s query=%r intent=%s filters=%s",
        tenant_id, query[:80], intent.type, intent.filters,
    )

    # ── Route: exact ID lookup ────────────────────────────────────────────────
    if intent.type == "id_lookup" and intent.id_string:
        docs = await _id_lookup(intent.id_string, tenant_id)
        if docs:
            return docs
        # No exact match — fall through to semantic search so we still return something

    # ── Route: aggregate count ────────────────────────────────────────────────
    # Run the COUNT query, then also do semantic retrieval for example docs
    count_doc: Document | None = None
    if intent.type == "aggregate":
        try:
            count_doc = await _aggregate_count(query, tenant_id, intent.filters, allowed_sources)
        except Exception as exc:
            logger.warning("hybrid_retrieve: aggregate_count failed: %s", exc)

    # ── Apply intent filters to narrow candidate set ──────────────────────────
    # Translates state/author/source_type/since into a source_id allow-list.
    # Intersected with the user's existing permission set when restricted.
    effective_sources = allowed_sources
    if intent.filters:
        try:
            effective_sources = await _resolve_filter_source_ids(
                tenant_id, intent.filters, allowed_sources
            )
        except Exception as exc:
            logger.warning("hybrid_retrieve: filter resolution failed: %s", exc)
        if effective_sources is not None and not effective_sources:
            # Filters matched nothing — return count doc alone (if any), else empty
            return [count_doc] if count_doc else []

    try:
        query_vector = await _embed_query(query)
    except Exception as exc:
        logger.error("hybrid_retrieve: embed failed: %s", exc)
        return [count_doc] if count_doc else []

    dense_docs, bm25_docs = [], []

    import time

    try:
        t0 = time.monotonic()
        dense_docs = await _dense_search(query_vector, collection, top_k=k * 2, allowed_sources=effective_sources)
        telemetry.record_latency_span(
            "retrieval_dense",
            latency_ms=(time.monotonic() - t0) * 1000,
            metadata={"docs": len(dense_docs), "collection": collection},
        )
        logger.info("hybrid_retrieve: dense=%d", len(dense_docs))
    except Exception as exc:
        logger.warning("hybrid_retrieve: dense search failed: %s", exc)

    try:
        t0 = time.monotonic()
        bm25_docs = await _bm25_search(query, tenant_id, top_k=k * 2, allowed_sources=effective_sources)
        telemetry.record_latency_span(
            "retrieval_bm25",
            latency_ms=(time.monotonic() - t0) * 1000,
            metadata={"docs": len(bm25_docs)},
        )
        logger.info("hybrid_retrieve: bm25=%d", len(bm25_docs))
    except Exception as exc:
        logger.warning("hybrid_retrieve: BM25 search failed: %s", exc)

    if not dense_docs and not bm25_docs:
        logger.warning("hybrid_retrieve: both searches returned 0 docs")
        return [count_doc] if count_doc else []

    merged = _rrf_merge(dense_docs, bm25_docs)
    logger.info("hybrid_retrieve: after RRF merge=%d", len(merged))

    t0 = time.monotonic()
    final = await _cohere_rerank(query, merged, top_k=k)
    telemetry.record_latency_span(
        "retrieval_rerank",
        latency_ms=(time.monotonic() - t0) * 1000,
        metadata={"candidates_in": len(merged), "docs_out": len(final)},
    )

    # For temporal queries ("last PR", "latest commit", "most recent deploy"),
    # re-sort candidates by date so the LLM sees the newest documents first.
    # Documents without a date are pushed to the end.
    if _is_temporal_query(query):
        final.sort(
            key=lambda d: d.metadata.get("created_at") or "",
            reverse=True,
        )
        logger.info("hybrid_retrieve: applied recency sort for temporal query")

    # Prepend the aggregate count doc so the LLM sees the exact number first
    if count_doc:
        final = [count_doc] + final[:k - 1]

    logger.info("hybrid_retrieve: final=%d docs", len(final))
    return final
