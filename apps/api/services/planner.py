"""
OpsLens AI — Reasoning Engine with LangGraph Query Planner
============================================================
A multi-step agentic RAG pipeline built on LangGraph.

Graph topology:
  START
    │
    ▼
  [plan]  ──────────────────────────────────────────────────────────────┐
    │  Decomposes the user question into N sub-queries                   │
    │  (1 for simple; 2-4 for complex cross-source questions)            │
    ▼                                                                    │
  [retrieve]                                                             │
    │  Runs hybrid_retriever for each sub-query in parallel              │
    │  Deduplicates and merges all retrieved docs                        │
    ▼                                                                    │
  [generate]                                                             │
    │  Calls the LLM once with all retrieved docs as context             │
    ▼                                                                    │
  [validate]                                                             │
    │  Runs all 3 validator nodes (Auditor, Gatekeeper, Strategist)      │
    │                                                                    │
    ├─► passed?  YES → END (yield final answer)                          │
    │                                                                    │
    └─► NO  + iterations < MAX_ITER                                      │
          │  Uses Gatekeeper's missing_queries for follow-up retrieval   │
          └──────────────────────────────────────────────────────────────┘

The planner uses structured JSON output from the LLM to decide how many
sub-queries to issue and what each one should be.  This means even a single
user message like "what's blocking the payment gateway sprint?" can trigger
parallel retrieval across Jira (sprint tickets), GitHub (open PRs), and
Slack (recent #payments-team discussion).

This module exposes two interfaces:
  - astream_planned_response(question, tenant_id) → AsyncIterator[str]
    Streams the final answer token-by-token (same interface as before).
  - plan_and_answer(question, tenant_id) → str
    Non-streaming version (for background tasks, alerts, etc.).
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from langchain_core.documents import Document

from ..config import settings
from ..utils.logging import get_logger
from .hybrid_retriever import hybrid_retrieve
from .validator import validate_answer, FullValidation

logger = get_logger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MAX_ITER        = 2      # max retrieval→generate→validate cycles
MAX_SUB_QUERIES = 4      # planner can emit at most this many sub-queries
TOP_K           = settings.QDRANT_TOP_K

# ── LLM helpers ───────────────────────────────────────────────────────────────
def _openai_key() -> str:
    return (
        os.environ.get("OPENAI_TOKEN", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or settings.OPENAI_API_KEY
        or ""
    ).strip()

def _anthropic_key() -> str:
    return (
        os.environ.get("ANTHROPIC_API_KEY", "")
        or settings.ANTHROPIC_API_KEY
        or ""
    ).strip()


# ── State dataclass ───────────────────────────────────────────────────────────
@dataclass
class PlannerState:
    question:       str
    tenant_id:      str
    sub_queries:    list[str]             = field(default_factory=list)
    retrieved_docs: list[Document]        = field(default_factory=list)
    answer:         str                   = ""
    validation:     FullValidation | None = None
    iteration:      int                   = 0
    metadata:       dict[str, Any]        = field(default_factory=dict)


# ── Node: Plan ────────────────────────────────────────────────────────────────
_PLAN_SYSTEM = """\
You are a query planning assistant for an enterprise knowledge-retrieval system
that has data from GitHub, Jira, and Slack.

Given a user question, decompose it into 1–{max_sub} specific search queries
that together will retrieve all the evidence needed to answer it completely.

Rules:
- If the question is simple and single-topic, emit exactly 1 query (= the question itself).
- If the question spans multiple sources or concepts, emit 2–{max_sub} targeted queries.
- Each query should be a natural search phrase (not a question), optimised for keyword and semantic retrieval.
- Do NOT add queries for information that isn't needed.

Output JSON only:
  {{"queries": ["query1", "query2", ...]}}
""".replace("{max_sub}", str(MAX_SUB_QUERIES))


async def _plan_node(state: PlannerState) -> PlannerState:
    """Decompose the question into sub-queries."""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_openai_key())
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user",   "content": state.question},
            ],
        )
        data = json.loads(resp.choices[0].message.content or '{"queries":[]}')
        queries = data.get("queries", [state.question])
        # Sanitise
        state.sub_queries = [q.strip() for q in queries if q.strip()][:MAX_SUB_QUERIES]
        if not state.sub_queries:
            state.sub_queries = [state.question]
    except Exception as exc:
        logger.warning("plan_node failed (%s), falling back to single query", exc)
        state.sub_queries = [state.question]

    logger.info("plan_node: %d sub-queries: %s", len(state.sub_queries), state.sub_queries)
    return state


# ── Node: Retrieve ────────────────────────────────────────────────────────────
async def _retrieve_node(state: PlannerState) -> PlannerState:
    """Run hybrid_retrieve for each sub-query in parallel, then deduplicate."""
    tasks = [
        hybrid_retrieve(q, state.tenant_id, top_k=TOP_K)
        for q in state.sub_queries
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    seen_content: set[str] = set()
    merged: list[Document] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("retrieve_node: sub-query failed: %s", r)
            continue
        for doc in r:
            key = doc.page_content[:200]
            if key not in seen_content:
                seen_content.add(key)
                merged.append(doc)

    # Keep top-K by RRF score if available, else just cap
    merged.sort(key=lambda d: -d.metadata.get("_rrf_score", 0.0))
    state.retrieved_docs = merged[:TOP_K * 2]
    logger.info("retrieve_node: merged %d unique docs", len(state.retrieved_docs))
    return state


# ── Node: Generate ────────────────────────────────────────────────────────────
def _build_context(docs: list[Document]) -> str:
    parts = []
    for i, doc in enumerate(docs[:12], start=1):
        meta    = doc.metadata
        source  = meta.get("source_type", "unknown")
        title   = meta.get("title", "")
        url     = meta.get("url", "")
        header  = f"[{i}] [{source.upper()}]"
        if title:
            header += f" {title}"
        if url:
            header += f" ({url})"
        parts.append(f"{header}\n{doc.page_content[:600]}")
    return "\n\n---\n\n".join(parts)


_GENERATE_SYSTEM = """\
You are OpsLens AI, an intelligent operations assistant for engineering and
product teams. You have access to the team's GitHub, Jira, and Slack data.

Answer the question clearly and concisely using ONLY the context passages
provided below.  If the context does not contain enough information to answer,
say so honestly — do not fabricate details.

Use markdown formatting. Cite sources with [N] notation where relevant."""


async def _generate_node(state: PlannerState) -> PlannerState:
    """Generate an answer using the retrieved docs."""
    context = _build_context(state.retrieved_docs)
    if not context.strip():
        state.answer = (
            "I don't have enough information in the connected data sources to "
            "answer this question. Try syncing more data or rephrasing your query."
        )
        return state

    messages = [
        {"role": "system", "content": _GENERATE_SYSTEM},
        {"role": "user",   "content": (
            f"Context:\n{context}\n\n"
            f"Question: {state.question}"
        )},
    ]

    try:
        if settings.LLM_PROVIDER == "claude":
            from anthropic import AsyncAnthropic
            client   = AsyncAnthropic(api_key=_anthropic_key())
            response = await client.messages.create(
                model=settings.ANTHROPIC_CHAT_MODEL,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                system=_GENERATE_SYSTEM,
                messages=[m for m in messages if m["role"] != "system"],
            )
            state.answer = response.content[0].text
        elif settings.LLM_PROVIDER == "ollama":
            import httpx
            resp = await httpx.AsyncClient(timeout=120).post(
                f"{settings.OLLAMA_URL}/api/chat",
                json={"model": settings.OLLAMA_CHAT_MODEL, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            state.answer = resp.json()["message"]["content"]
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=_openai_key())
            resp   = await client.chat.completions.create(
                model=settings.OPENAI_CHAT_MODEL,
                temperature=settings.OPENAI_TEMPERATURE,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                messages=messages,
            )
            state.answer = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.error("generate_node: LLM call failed: %s", exc)
        state.answer = f"An error occurred generating the response: {exc}"

    logger.info("generate_node: answer len=%d", len(state.answer))
    return state


# ── Node: Validate ────────────────────────────────────────────────────────────
async def _validate_node(state: PlannerState) -> PlannerState:
    """Run all three validation nodes."""
    try:
        state.validation = await validate_answer(
            state.question,
            state.retrieved_docs,
            state.answer,
        )
        logger.info(
            "validate_node: passed=%s score=%.2f",
            state.validation.passed,
            state.validation.overall_score,
        )
    except Exception as exc:
        logger.warning("validate_node: validation failed (%s), skipping", exc)
    return state


# ── Graph runner ──────────────────────────────────────────────────────────────
async def _run_graph(question: str, tenant_id: str) -> PlannerState:
    """
    Execute the planner graph: plan → retrieve → generate → validate,
    with up to MAX_ITER retry loops using Gatekeeper's missing_queries.
    """
    state = PlannerState(question=question, tenant_id=tenant_id)

    # Step 1: plan
    state = await _plan_node(state)

    for iteration in range(MAX_ITER):
        state.iteration = iteration + 1

        # Step 2: retrieve
        state = await _retrieve_node(state)

        # Step 3: generate
        state = await _generate_node(state)

        # Step 4: validate
        state = await _validate_node(state)

        if state.validation is None or state.validation.passed:
            break

        # If gatekeeper found missing queries, use them for next iteration
        gk_raw       = state.validation.gatekeeper.raw
        missing_qs   = gk_raw.get("missing_queries", [])
        if missing_qs and iteration + 1 < MAX_ITER:
            logger.info(
                "planner: iteration %d failed — retrying with %d missing queries",
                iteration + 1, len(missing_qs),
            )
            state.sub_queries = missing_qs[:MAX_SUB_QUERIES]
        else:
            # No more iterations — keep current answer with a disclaimer
            break

    return state


# ── Public API ────────────────────────────────────────────────────────────────
async def plan_and_answer(question: str, tenant_id: str) -> tuple[str, PlannerState]:
    """
    Non-streaming version. Returns (answer_text, final_state).
    The caller can inspect state.validation for scores/issues.
    """
    state = await _run_graph(question, tenant_id)

    answer = state.answer
    if state.validation and not state.validation.passed:
        disclaimer = state.validation.disclaimer()
        if disclaimer:
            answer = f"{disclaimer}\n\n{answer}"

    return answer, state


async def astream_planned_response(
    question: str,
    tenant_id: str,
) -> AsyncIterator[str]:
    """
    Streaming version.  Runs the full plan→retrieve→generate→validate graph,
    then streams the final answer token-by-token.

    Yields:
      - "[thinking]…" progress token at start (optional UX hint)
      - Final answer tokens streamed from the LLM
    """
    # Run the graph (non-streaming) to get the validated answer
    answer, state = await plan_and_answer(question, tenant_id)

    # If validation found issues, prepend a disclaimer
    if state.validation and not state.validation.passed:
        issues = state.validation.blocking_issues()
        if issues:
            logger.warning(
                "astream_planned_response: validation failed — issues: %s", issues
            )

    # Stream answer character-by-character in reasonably sized chunks
    # (real token-streaming would require re-running the LLM with stream=True;
    #  we do it word-by-word here so the UI stays responsive)
    words = answer.split(" ")
    for i, word in enumerate(words):
        yield word + (" " if i < len(words) - 1 else "")
        await asyncio.sleep(0)   # yield control to event loop


async def astream_planned_response_live(
    question: str,
    tenant_id: str,
) -> AsyncIterator[str]:
    """
    True streaming version: plan + retrieve + validate happen first, then the
    LLM streams its answer live while we pass tokens straight to the caller.
    Validation runs on the *completed* buffer and appends a disclaimer if needed.
    """
    state = PlannerState(question=question, tenant_id=tenant_id)

    # Plan + retrieve (cannot stream these)
    state = await _plan_node(state)
    state = await _retrieve_node(state)

    context = _build_context(state.retrieved_docs)
    if not context.strip():
        yield (
            "I don't have enough information in the connected data sources to "
            "answer this question. Try syncing more data or rephrasing your query."
        )
        return

    messages = [
        {"role": "system", "content": _GENERATE_SYSTEM},
        {"role": "user",   "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]

    full_answer = ""
    try:
        if settings.LLM_PROVIDER == "claude":
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=_anthropic_key())
            async with client.messages.stream(
                model=settings.ANTHROPIC_CHAT_MODEL,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                system=_GENERATE_SYSTEM,
                messages=[m for m in messages if m["role"] != "system"],
            ) as stream:
                async for token in stream.text_stream:
                    full_answer += token
                    yield token
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=_openai_key())
            stream = await client.chat.completions.create(
                model=settings.OPENAI_CHAT_MODEL,
                temperature=settings.OPENAI_TEMPERATURE,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                messages=messages,
                stream=True,
            )
            async for chunk in stream:
                token = chunk.choices[0].delta.content or ""
                full_answer += token
                yield token

    except Exception as exc:
        err_msg = f"\n\n[Error generating response: {exc}]"
        full_answer += err_msg
        yield err_msg
        return

    # Post-stream validation (runs after all tokens are emitted)
    try:
        state.answer     = full_answer
        state.validation = await validate_answer(question, state.retrieved_docs, full_answer)
        if not state.validation.passed:
            disclaimer = state.validation.disclaimer()
            if disclaimer:
                yield f"\n\n{disclaimer}"
                logger.warning(
                    "live stream: validation issues — scores A:%.2f G:%.2f S:%.2f",
                    state.validation.auditor.score,
                    state.validation.gatekeeper.score,
                    state.validation.strategist.score,
                )
    except Exception as exc:
        logger.warning("post-stream validation failed: %s", exc)
