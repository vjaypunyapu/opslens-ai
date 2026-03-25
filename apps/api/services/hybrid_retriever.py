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

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
TOP_K         = settings.QDRANT_TOP_K   # e.g. 10
RRF_K         = 60                      # RRF constant (standard = 60)
COHERE_MODEL  = "rerank-english-v3.0"

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
) -> list[Document]:
    """Hit Qdrant's /points/search endpoint directly via httpx."""
    base = _qdrant_base()
    payload = {
        "vector": query_vector,
        "limit": top_k,
        "with_payload": True,
        "with_vector": False,
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


# ── BM25 search ───────────────────────────────────────────────────────────────
async def _bm25_search(
    query: str,
    tenant_id: str,
    top_k: int,
) -> list[Document]:
    """
    Build an in-memory BM25 index from CanonicalDocument rows for this tenant,
    then return the top-k matches.
    Falls back to empty list if rank_bm25 is not installed or DB is empty.
    """
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning("rank_bm25 not installed — BM25 search disabled")
        return []

    import sqlalchemy as sa
    from ..db.models import CanonicalDocument
    from ..db.session import AsyncSessionFactory as async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(
            sa.select(CanonicalDocument.id, CanonicalDocument.content, CanonicalDocument.title,
                      CanonicalDocument.url, CanonicalDocument.source_type, CanonicalDocument.author,
                      CanonicalDocument.source_created_at)
            .where(CanonicalDocument.tenant_id == uuid.UUID(tenant_id))
            .limit(5000)   # cap for memory safety
        )
        rows = result.fetchall()

    if not rows:
        return []

    # Tokenise corpus
    tokenised = [row.content.lower().split() for row in rows]
    bm25      = BM25Okapi(tokenised)
    scores    = bm25.get_scores(query.lower().split())

    # Get top-k indices
    top_indices = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]

    docs: list[Document] = []
    for idx in top_indices:
        if scores[idx] <= 0:
            continue
        row = rows[idx]
        docs.append(Document(
            page_content=row.content,
            metadata={
                "document_id":  str(row.id),
                "source_type":  row.source_type,
                "title":        row.title or "",
                "url":          row.url or "",
                "author":       row.author or "",
                "created_at":   row.source_created_at.isoformat() if row.source_created_at else None,
                "_bm25_score":  float(scores[idx]),
            },
        ))
    return docs


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
) -> list[Document]:
    """
    Full hybrid retrieval pipeline:
      1. Embed query
      2. Dense Qdrant search
      3. BM25 search over tenant corpus
      4. RRF fusion
      5. Cohere rerank (optional)
    Returns a list of LangChain Document objects.
    """
    k = top_k or TOP_K
    collection = f"{settings.QDRANT_COLLECTION_PREFIX}{tenant_id}"

    logger.info("hybrid_retrieve: tenant=%s query=%r top_k=%d", tenant_id, query[:80], k)

    try:
        query_vector = await _embed_query(query)
    except Exception as exc:
        logger.error("hybrid_retrieve: embed failed: %s", exc)
        return []

    dense_docs, bm25_docs = [], []

    import time

    try:
        t0 = time.monotonic()
        dense_docs = await _dense_search(query_vector, collection, top_k=k * 2)
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
        bm25_docs = await _bm25_search(query, tenant_id, top_k=k * 2)
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
        return []

    merged = _rrf_merge(dense_docs, bm25_docs)
    logger.info("hybrid_retrieve: after RRF merge=%d", len(merged))

    t0 = time.monotonic()
    final = await _cohere_rerank(query, merged, top_k=k)
    telemetry.record_latency_span(
        "retrieval_rerank",
        latency_ms=(time.monotonic() - t0) * 1000,
        metadata={"candidates_in": len(merged), "docs_out": len(final)},
    )
    logger.info("hybrid_retrieve: final=%d docs", len(final))
    return final
