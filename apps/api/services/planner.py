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
from . import telemetry

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


# ── Model tiers ───────────────────────────────────────────────────────────────
# "mini"  → gpt-4o-mini  (~87% cheaper, used for direct lookups)
# "full"  → gpt-4o        (used for reasoning, cross-source, analysis)
_MODEL_MINI = "gpt-4o-mini"
_MODEL_FULL = "gpt-4o"          # overridden by settings.OPENAI_CHAT_MODEL at runtime


# ── State dataclass ───────────────────────────────────────────────────────────
@dataclass
class PlannerState:
    question:        str
    tenant_id:       str
    sub_queries:     list[str]             = field(default_factory=list)
    retrieved_docs:  list[Document]        = field(default_factory=list)
    answer:          str                   = ""
    validation:      FullValidation | None = None
    iteration:       int                   = 0
    model_tier:      str                   = "full"   # "mini" | "full"
    metadata:        dict[str, Any]        = field(default_factory=dict)
    # None = admin/unrestricted; [] = no access; [...] = restricted source list
    allowed_sources: list[dict] | None     = None


# ── Node: Plan ────────────────────────────────────────────────────────────────
_PLAN_SYSTEM = """\
You are a query planning assistant for an enterprise knowledge-retrieval system
that has data from GitHub, Jira, and Slack.

Given a user question, do two things in a single JSON response:

1. DECOMPOSE into 1–{max_sub} specific search queries that together retrieve all
   evidence needed to answer the question completely.
   - Simple, single-topic questions → exactly 1 query.
   - Questions spanning multiple sources or concepts → 2–{max_sub} targeted queries.
   - Each query should be a natural search phrase optimised for keyword and semantic retrieval.

2. CLASSIFY the complexity as either "simple" or "complex":
   - "simple": direct factual lookup, single data source likely sufficient, no
     reasoning or comparison required.
     Examples: "status of AUTH-123", "who owns the payments service",
               "latest commit on main", "is PROJ-45 resolved?"
   - "complex": requires reasoning, comparison, cross-source analysis, time-range
     synthesis, or the answer depends on multiple interconnected facts.
     Examples: "what's blocking the sprint", "why did error rate spike yesterday",
               "compare PR review time across teams", "summarise all open bugs"

Output JSON only — two keys, nothing else:
  {{"queries": ["query1", ...], "complexity": "simple" | "complex"}}
""".replace("{max_sub}", str(MAX_SUB_QUERIES))


async def _plan_node(state: PlannerState) -> PlannerState:
    """Decompose the question into sub-queries."""
    import time
    t0 = time.monotonic()
    model = "gpt-4o-mini"
    in_tok = out_tok = 0
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=_openai_key())
        resp = await client.chat.completions.create(
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user",   "content": state.question},
            ],
        )
        in_tok  = resp.usage.prompt_tokens     if resp.usage else 0
        out_tok = resp.usage.completion_tokens if resp.usage else 0
        data = json.loads(resp.choices[0].message.content or '{"queries":[]}')
        queries = data.get("queries", [state.question])
        state.sub_queries = [q.strip() for q in queries if q.strip()][:MAX_SUB_QUERIES]
        if not state.sub_queries:
            state.sub_queries = [state.question]

        # Route model tier from classifier output.
        # Fall back to "mini" when there's only 1 sub-query even if unclassified —
        # a single focused query is almost always a direct lookup.
        complexity = data.get("complexity", "")
        if complexity == "simple" or (not complexity and len(state.sub_queries) == 1):
            state.model_tier = "mini"
        else:
            state.model_tier = "full"

    except Exception as exc:
        logger.warning("plan_node failed (%s), falling back to single query", exc)
        state.sub_queries = [state.question]
        state.model_tier  = "full"   # safe default on error
    finally:
        telemetry.record_llm_span(
            "planner", model, in_tok, out_tok,
            latency_ms=(time.monotonic() - t0) * 1000,
            metadata={"sub_queries": len(state.sub_queries), "model_tier": state.model_tier},
        )

    logger.info("plan_node: %d sub-queries tier=%s queries=%s",
                len(state.sub_queries), state.model_tier, state.sub_queries)
    return state


# ── Node: Retrieve ────────────────────────────────────────────────────────────
async def _retrieve_node(state: PlannerState) -> PlannerState:
    """Run hybrid_retrieve for each sub-query in parallel, then deduplicate."""
    import time
    t0 = time.monotonic()

    tasks = [
        hybrid_retrieve(q, state.tenant_id, top_k=TOP_K, allowed_sources=state.allowed_sources)
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

    merged.sort(key=lambda d: -d.metadata.get("_rrf_score", 0.0))
    state.retrieved_docs = merged[:TOP_K * 2]

    telemetry.record_latency_span(
        "retrieval",
        latency_ms=(time.monotonic() - t0) * 1000,
        metadata={
            "sub_queries":   len(state.sub_queries),
            "docs_returned": len(state.retrieved_docs),
        },
    )
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
    import time
    t0 = time.monotonic()
    in_tok = out_tok = 0

    context = _build_context(state.retrieved_docs)
    if not context.strip():
        state.answer = (
            "I don't have enough information in the connected data sources to "
            "answer this question. Try syncing more data or rephrasing your query."
        )
        return state

    messages = [
        {"role": "system", "content": _GENERATE_SYSTEM},
        {"role": "user",   "content": f"Context:\n{context}\n\nQuestion: {state.question}"},
    ]

    # ── Model routing ─────────────────────────────────────────────────────────
    # Simple / single-source queries use gpt-4o-mini (~87% cheaper per token).
    # Complex / multi-source / reasoning queries use the full model.
    # For non-OpenAI providers we don't have a cheap variant — use the configured model.
    if settings.LLM_PROVIDER == "openai":
        model_name = _MODEL_MINI if state.model_tier == "mini" else settings.OPENAI_CHAT_MODEL
    elif settings.LLM_PROVIDER == "claude":
        model_name = settings.ANTHROPIC_CHAT_MODEL
    else:
        model_name = settings.OLLAMA_CHAT_MODEL

    try:
        if settings.LLM_PROVIDER == "claude":
            from anthropic import AsyncAnthropic
            client   = AsyncAnthropic(api_key=_anthropic_key())
            response = await client.messages.create(
                model=model_name,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                system=_GENERATE_SYSTEM,
                messages=[m for m in messages if m["role"] != "system"],
            )
            state.answer = response.content[0].text
            in_tok  = response.usage.input_tokens
            out_tok = response.usage.output_tokens
        elif settings.LLM_PROVIDER == "ollama":
            import httpx
            resp = await httpx.AsyncClient(timeout=120).post(
                f"{settings.OLLAMA_URL}/api/chat",
                json={"model": model_name, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            state.answer = resp.json()["message"]["content"]
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=_openai_key())
            resp   = await client.chat.completions.create(
                model=model_name,
                temperature=settings.OPENAI_TEMPERATURE,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                messages=messages,
            )
            state.answer = resp.choices[0].message.content or ""
            in_tok  = resp.usage.prompt_tokens     if resp.usage else 0
            out_tok = resp.usage.completion_tokens if resp.usage else 0
    except Exception as exc:
        logger.error("generate_node: LLM call failed: %s", exc)
        state.answer = f"An error occurred generating the response: {exc}"
    finally:
        telemetry.record_llm_span(
            "generation", model_name, in_tok, out_tok,
            latency_ms=(time.monotonic() - t0) * 1000,
            metadata={"context_docs": len(state.retrieved_docs), "tier": state.model_tier},
        )

    logger.info("generate_node: tier=%s model=%s answer_len=%d in=%d out=%d",
                state.model_tier, model_name, len(state.answer), in_tok, out_tok)
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
async def _run_graph(
    question: str,
    tenant_id: str,
    allowed_sources: list[dict] | None = None,
) -> PlannerState:
    """
    Execute the planner graph: plan → retrieve → generate → validate,
    with up to MAX_ITER retry loops using Gatekeeper's missing_queries.

    allowed_sources: passed from the auth layer via permissions.get_allowed_sources().
        None  → unrestricted (admin)
        []    → no access
        [...] → restricted to these source_ids
    """
    state = PlannerState(question=question, tenant_id=tenant_id, allowed_sources=allowed_sources)

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
async def plan_and_answer(
    question: str,
    tenant_id: str,
    allowed_sources: list[dict] | None = None,
) -> tuple[str, PlannerState]:
    """
    Non-streaming version. Returns (answer_text, final_state).
    The caller can inspect state.validation for scores/issues.

    allowed_sources: from permissions.get_allowed_sources(). None = unrestricted.
    """
    state = await _run_graph(question, tenant_id, allowed_sources=allowed_sources)

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
