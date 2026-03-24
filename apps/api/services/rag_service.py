"""
OpsLens AI — RAG Service
==========================
Streaming Retrieval-Augmented Generation pipeline.

Architecture:
    User query
        │
        ▼
    Embed query (text-embedding-3-small)
        │
        ▼
    Qdrant semantic search (top-K, per-tenant, optional source filter)
        │
        ▼
    Context assembly (trim to 128k tokens)
        │
        ▼
    GPT-4o via LCEL chain (streaming)
        │
        ▼
    Source citations + SSE token stream → client
"""
from __future__ import annotations

import json
from typing import AsyncGenerator, Any

from langchain_core.messages import AIMessage, HumanMessage, BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
import urllib.parse as _urlparse

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

# ── Module-level singletons ─────────────────────────────────────────────────
import os as _os
_openai_key = (
    _os.environ.get("OPENAI_TOKEN", "")
    or _os.environ.get("OPENAI_API_KEY", "")
    or (settings.OPENAI_API_KEY or "")
).strip()
_q_parsed  = _urlparse.urlparse(settings.QDRANT_URL)
_qdrant_key = (
    _os.environ.get("QDRANT_TOKEN", "")
    or _os.environ.get("QDRANT_API_KEY", "")
    or (settings.QDRANT_API_KEY or "")
).strip() or None
_qdrant_client = QdrantClient(
    host=_q_parsed.hostname,
    port=_q_parsed.port or 6333,
    https=(_q_parsed.scheme == "https"),
    api_key=_qdrant_key,
    prefer_grpc=False,
    timeout=10,
)

if settings.LLM_PROVIDER == "ollama":
    # Use Ollama's OpenAI-compatible endpoint — no extra package needed
    _embeddings = OpenAIEmbeddings(
        model=settings.OLLAMA_EMBED_MODEL,
        base_url=f"{settings.OLLAMA_URL}/v1",
        api_key="ollama",
        check_embedding_ctx_length=False,
    )
    _llm = ChatOpenAI(
        model=settings.OLLAMA_CHAT_MODEL,
        base_url=f"{settings.OLLAMA_URL}/v1",
        api_key="ollama",
        temperature=settings.OPENAI_TEMPERATURE,
        timeout=300,
    )
    logger.info("LLM provider: Ollama (%s) @ %s", settings.OLLAMA_CHAT_MODEL, settings.OLLAMA_URL)

elif settings.LLM_PROVIDER == "claude":
    # Claude for chat (200k context, enterprise BAA)
    # OpenAI for embeddings (Anthropic has no embeddings API)
    from langchain_anthropic import ChatAnthropic
    _embeddings = OpenAIEmbeddings(
        model=settings.OPENAI_EMBED_MODEL,
        openai_api_key=_openai_key,
    )
    _llm = ChatAnthropic(
        model=settings.ANTHROPIC_CHAT_MODEL,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        temperature=settings.OPENAI_TEMPERATURE,
        max_tokens=settings.OPENAI_MAX_TOKENS,
        streaming=True,
    )
    logger.info("LLM provider: Claude (%s) + OpenAI embeddings", settings.ANTHROPIC_CHAT_MODEL)

else:  # openai (default)
    _embeddings = OpenAIEmbeddings(
        model=settings.OPENAI_EMBED_MODEL,
        openai_api_key=_openai_key,
    )
    _llm = ChatOpenAI(
        model=settings.OPENAI_CHAT_MODEL,
        temperature=settings.OPENAI_TEMPERATURE,
        max_tokens=settings.OPENAI_MAX_TOKENS,
        streaming=True,
        openai_api_key=_openai_key,
    )
    logger.info("LLM provider: OpenAI (%s)", settings.OPENAI_CHAT_MODEL)

# ── System prompt ────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are OpsLens, the operational intelligence assistant for {company_name}.

Your purpose is to answer questions about the company's internal operations using ONLY
the context documents retrieved from internal tools (Slack, Jira, Google Drive, etc.).

RULES:
1. Base every answer strictly on the provided context documents.
2. Cite every factual claim inline using the format: [Source: <title>].
3. If the context does not contain enough information to answer, say:
   "I don't have sufficient data in the connected sources to answer this."
4. Be concise but thorough. Use bullet points for multi-part answers.
5. Format dates as: Month DD, YYYY.
6. Never make up data, metrics, or conclusions not supported by the context.
7. If multiple sources support the same point, cite all of them.

CONTEXT DOCUMENTS:
{context}
"""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{question}"),
])


class RagService:
    """
    Encapsulates the full RAG pipeline.
    Instantiated once and injected via FastAPI dependency.
    """

    def get_retriever(
        self,
        tenant_id: str,
        source_types: list[str] | None = None,
    ):
        """Build a tenant-scoped Qdrant retriever with optional source filtering."""
        collection_name = f"{settings.QDRANT_COLLECTION_PREFIX}{tenant_id}"
        store = QdrantVectorStore(
            client=_qdrant_client,
            collection_name=collection_name,
            embedding=_embeddings,
        )
        search_kwargs: dict[str, Any] = {"k": settings.QDRANT_TOP_K}
        if source_types:
            search_kwargs["filter"] = Filter(
                must=[
                    FieldCondition(
                        key="source_type",
                        match=MatchAny(any=source_types),
                    )
                ]
            )
        return store.as_retriever(
            search_type="similarity",
            search_kwargs=search_kwargs,
        )

    @staticmethod
    def _format_docs(docs: list) -> str:
        """Format retrieved docs into a structured context string."""
        if not docs:
            return "No relevant documents found in connected sources."
        formatted = []
        for i, doc in enumerate(docs, start=1):
            meta = doc.metadata
            formatted.append(
                f"[{i}] Source: {meta.get('title', 'Untitled')} "
                f"({meta.get('source_type', 'unknown')})\n"
                f"URL: {meta.get('url', 'N/A')}\n"
                f"Author: {meta.get('author', 'Unknown')} | "
                f"Date: {meta.get('created_at', 'N/A')}\n"
                f"---\n{doc.page_content}\n"
            )
        return "\n\n".join(formatted)

    @staticmethod
    def _build_history(raw: list[dict]) -> list[BaseMessage]:
        """Convert raw message dicts to LangChain message objects."""
        messages = []
        for m in raw:
            if m["role"] == "user":
                messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                messages.append(AIMessage(content=m["content"]))
        return messages

    async def stream(
        self,
        tenant_id: str,
        company_name: str,
        question: str,
        history: list[dict],
        source_types: list[str] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Run the RAG pipeline and yield structured SSE events.

        Yields:
            {"type": "token",   "data": "<text>"}
            {"type": "sources", "data": [{"title", "url", "source_type", "snippet"}]}
            {"type": "done",    "latency_ms": <int>}
            {"type": "error",   "message": "<str>"}
        """
        import time
        start = time.monotonic()
        collection = f"opslens_{tenant_id}"
        logger.error("RAG_DEBUG: stream called tenant=%s collection=%s question=%r",
                     tenant_id, collection, question[:80])

        try:
            retriever = self.get_retriever(tenant_id, source_types)
            lc_history = self._build_history(history)

            # Retrieve relevant documents (non-streaming pre-fetch).
            # If the Qdrant collection doesn't exist yet (no data synced), treat as empty.
            try:
                docs = await retriever.ainvoke(question)
                logger.error("RAG_DEBUG: retrieved %d docs collection=%s", len(docs), collection)
            except Exception as qdrant_exc:
                msg = str(qdrant_exc).lower()
                logger.error("RAG: Qdrant retrieval error for tenant=%s: %s", tenant_id, qdrant_exc, exc_info=True)
                if "not found" in msg or "doesn't exist" in msg or "collection" in msg:
                    logger.warning("Qdrant collection not found for tenant=%s — no data synced yet", tenant_id)
                    docs = []
                else:
                    raise
            context_str = self._format_docs(docs)
            logger.info("RAG context preview for tenant=%s: %s", tenant_id, context_str[:600])

            # Build and run the LCEL chain
            chain = (
                {
                    "context":      lambda _: context_str,
                    "company_name": lambda _: company_name,
                    "question":     RunnablePassthrough(),
                    "history":      lambda _: lc_history,
                }
                | _PROMPT
                | _llm
                | StrOutputParser()
            )

            async for token in chain.astream(question):
                yield {"type": "token", "data": token}

            # Emit source metadata
            sources = [
                {
                    "title":       doc.metadata.get("title", "Untitled"),
                    "url":         doc.metadata.get("url", ""),
                    "source_type": doc.metadata.get("source_type", ""),
                    "snippet":     doc.page_content[:350],
                }
                for doc in docs
            ]
            yield {"type": "sources", "data": sources}
            yield {"type": "done", "latency_ms": int((time.monotonic() - start) * 1000)}

        except Exception as exc:
            logger.exception("RAG pipeline error for tenant=%s", tenant_id)
            yield {"type": "error", "message": str(exc)}


# ── FastAPI dependency ────────────────────────────────────────────────────────
_rag_service = RagService()


def get_rag_service() -> RagService:
    return _rag_service
