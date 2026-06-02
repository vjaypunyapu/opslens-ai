"""
OpsLens AI — RAG Service
==========================
Streaming Retrieval-Augmented Generation pipeline.

Architecture (v3 — Multi-Agent):
    User query
        │
        ▼
    [Supervisor Agent] Routes intent to specialist(s)
        │
        ├──► [Research Agent]  Hybrid RAG retrieval + LLM generation
        ├──► [Insight Agent]   Pattern detection, trend analysis
        ├──► [Alert Agent]     Active alerts, severity summaries
        └──► [Incident Agent]  RRT briefs, deployment timelines
        │
        ▼
    [Synthesizer] Merges multi-agent outputs
        │
        ▼
    [Validator] Auditor + Gatekeeper + Strategist nodes
        │
        ▼
    Source citations + SSE token stream → client

Falls back to the legacy planner path if the agent graph raises.
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
        allowed_sources: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Run the full RAG pipeline and yield structured SSE events.

        v2: Uses the planner (plan → hybrid retrieve → generate → validate).
        Falls back to the legacy Qdrant-only path if the planner raises.

        Yields:
            {"type": "token",   "data": "<text>"}
            {"type": "sources", "data": [{"title", "url", "source_type", "snippet"}]}
            {"type": "done",    "latency_ms": <int>}
            {"type": "error",   "message": "<str>"}
        """
        import time
        import uuid as _uuid
        from . import telemetry

        start    = time.monotonic()
        trace_id = str(_uuid.uuid4())
        telemetry.start_trace(trace_id, tenant_id, question)
        logger.info("RAG: trace=%s tenant=%s question=%r", trace_id[:8], tenant_id, question[:80])

        validation_passed = True
        retry_count       = 0
        error_msg         = ""

        try:
            from ..agents.graph import get_agent_graph

            agent_graph = get_agent_graph()

            # Run through the multi-agent graph
            async for event in agent_graph.stream(
                question=question,
                tenant_id=tenant_id,
                company_name=company_name,
                history=history,
                allowed_sources=allowed_sources,
            ):
                if event["type"] == "done":
                    # Enrich done event with our trace_id
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    yield {
                        "type":       "done",
                        "latency_ms": elapsed_ms,
                        "trace_id":   trace_id,
                        "agents":     event.get("agents", []),
                    }
                else:
                    yield event

        except Exception as exc:
            # Fall back to the legacy single-agent planner path
            logger.warning(
                "Multi-agent graph failed (%s), falling back to planner — trace=%s",
                exc, trace_id[:8],
            )
            try:
                from .planner import plan_and_answer

                answer, plan_state = await plan_and_answer(
                    question, tenant_id,
                    allowed_sources=allowed_sources,
                    history=history,
                )
                words = answer.split(" ")
                for i, word in enumerate(words):
                    yield {"type": "token", "data": word + (" " if i < len(words) - 1 else "")}

                docs = plan_state.retrieved_docs
                sources = []
                for doc in docs:
                    raw_title   = doc.metadata.get("title", "") or ""
                    source_type = doc.metadata.get("source_type", "")
                    if not raw_title.strip():
                        snippet_preview = doc.page_content[:60].replace("\n", " ").strip()
                        raw_title = f"{source_type.capitalize()} log: {snippet_preview}…" if snippet_preview else f"{source_type.capitalize()} entry"
                    sources.append({
                        "title":       raw_title,
                        "url":         doc.metadata.get("url", ""),
                        "source_type": source_type,
                        "snippet":     doc.page_content[:350],
                    })
                yield {"type": "sources", "data": sources}
                yield {
                    "type":       "done",
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "trace_id":   trace_id,
                    "model_tier": plan_state.model_tier,
                    "fallback":   True,
                }
            except Exception as fallback_exc:
                error_msg = str(fallback_exc)
                logger.exception("Fallback planner also failed trace=%s", trace_id[:8])
                yield {"type": "error", "message": error_msg}

        finally:
            telemetry.finish_trace(
                trace_id,
                validation_passed=validation_passed,
                retry_count=retry_count,
                error=error_msg,
            )


# ── FastAPI dependency ────────────────────────────────────────────────────────
_rag_service = RagService()


def get_rag_service() -> RagService:
    return _rag_service
