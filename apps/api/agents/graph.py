"""
OpsLens AI — Multi-Agent LangGraph
=====================================
Supervisor-based multi-agent system using LangGraph StateGraph.

Graph topology:
  START
    │
    ▼
  [supervisor]  ── classifies intent → sets next_agents
    │
    ▼
  [dispatcher]  ── runs selected agents in parallel
    │             Each agent adds its output to agent_outputs
    ▼
  [synthesizer] ── merges multi-agent outputs into a final answer
    │             Runs Auditor + Gatekeeper + Strategist validation
    ▼
  END

Agents:
  research  → hybrid RAG retrieval + LLM answer (wraps existing planner)
  insight   → operational pattern detection (insights DB + on-demand detectors)
  alert     → active alert summaries and severity breakdowns
  incident  → RRT briefs + deployment/event timelines

Routing examples:
  "What's blocking the payment sprint?" → research
  "Are there any active critical alerts?" → alert
  "Show me churn risk trends" → insight
  "What happened during last night's outage?" → incident + research
  "Summarise our ops health" → research + insight + alert
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..services.guardrails import check_input, check_output
from ..utils.logging import get_logger
from .state import AgentState
from .tools import (
    get_active_alerts,
    get_alert_summary,
    get_incident_timeline,
    get_recent_insights,
    get_recent_rrt_briefs,
    run_insight_detector_now,
)

logger = get_logger(__name__)

# ── LLM helpers ───────────────────────────────────────────────────────────────

def _langsmith_client():
    """Return a LangSmith Client if tracing is enabled, else None."""
    if not settings.LANGCHAIN_TRACING_V2 or not settings.LANGCHAIN_API_KEY:
        return None
    try:
        from langsmith import Client
        return Client(api_key=settings.LANGCHAIN_API_KEY)
    except Exception:
        return None


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


async def _llm_json(system: str, user: str, model: str = "gpt-4o-mini", run_name: str = "llm_json") -> dict:
    """Call the LLM and return parsed JSON. Falls back to {} on error."""
    import time
    import uuid as _uuid
    ls = _langsmith_client()
    run_id = str(_uuid.uuid4()) if ls else None
    if ls and run_id:
        try:
            ls.create_run(
                id=run_id, name=run_name, run_type="llm",
                project_name=settings.LANGCHAIN_PROJECT,
                inputs={"system": system, "user": user},
            )
        except Exception:
            run_id = None

    t0 = time.monotonic()
    result: dict = {}
    error: str | None = None
    try:
        if settings.LLM_PROVIDER == "claude":
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=_anthropic_key())
            resp = await client.messages.create(
                model=settings.ANTHROPIC_CHAT_MODEL,
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            result = json.loads(resp.content[0].text)
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=_openai_key())
            resp = await client.chat.completions.create(
                model=model,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
            result = json.loads(resp.choices[0].message.content or "{}")
        return result
    except Exception as exc:
        logger.warning("_llm_json failed: %s", exc)
        error = str(exc)
        return {}
    finally:
        if ls and run_id:
            try:
                ls.update_run(run_id, outputs=result, error=error,
                              extra={"latency_ms": int((time.monotonic() - t0) * 1000)})
            except Exception:
                pass


async def _llm_text(system: str, user: str, model: str | None = None, run_name: str = "llm_text") -> str:
    """Call the LLM and return plain text."""
    import time
    import uuid as _uuid

    if settings.LLM_PROVIDER == "claude":
        m = settings.ANTHROPIC_CHAT_MODEL
    elif settings.LLM_PROVIDER == "ollama":
        m = settings.OLLAMA_CHAT_MODEL
    else:
        m = model or settings.OPENAI_CHAT_MODEL

    ls = _langsmith_client()
    run_id = str(_uuid.uuid4()) if ls else None
    if ls and run_id:
        try:
            ls.create_run(
                id=run_id, name=run_name, run_type="llm",
                project_name=settings.LANGCHAIN_PROJECT,
                inputs={"system": system, "user": user, "model": m},
            )
        except Exception:
            run_id = None

    t0 = time.monotonic()
    text = ""
    error: str | None = None
    try:
        if settings.LLM_PROVIDER == "claude":
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=_anthropic_key())
            resp = await client.messages.create(
                model=m, max_tokens=settings.OPENAI_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = resp.content[0].text
        elif settings.LLM_PROVIDER == "ollama":
            import httpx
            resp = await httpx.AsyncClient(timeout=120).post(
                f"{settings.OLLAMA_URL}/api/chat",
                json={"model": m, "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ], "stream": False},
            )
            resp.raise_for_status()
            text = resp.json()["message"]["content"]
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=_openai_key())
            resp = await client.chat.completions.create(
                model=m,
                temperature=settings.OPENAI_TEMPERATURE,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
            text = resp.choices[0].message.content or ""
        return text
    except Exception as exc:
        logger.error("_llm_text failed: %s", exc)
        error = str(exc)
        return f"[Error generating response: {exc}]"
    finally:
        if ls and run_id:
            try:
                ls.update_run(run_id, outputs={"text": text}, error=error,
                              extra={"latency_ms": int((time.monotonic() - t0) * 1000)})
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# Node: Supervisor
# ══════════════════════════════════════════════════════════════════════════════

_SUPERVISOR_SYSTEM = """\
You are the OpsLens AI Supervisor. Your job is to route the user's question
to the most appropriate specialized agent(s).

Available agents:
  "research"  — Retrieves and synthesises information from documents, code (GitHub),
                tickets (Jira), messages (Slack), logs, and other ingested data sources.
                Use for: factual questions, "what is X", "show me Y", "explain Z".

  "insight"   — Surfaces operational patterns, anomalies, and trends detected by AI.
                Use for: complaint spikes, feature trends, release correlations,
                engineering bottlenecks, churn risk, "what patterns", "are there trends".

  "alert"     — Reports on active alerts, recent alert history, severity breakdowns.
                Use for: "any active alerts", "critical incidents", "alert status",
                "what's firing", "error rate", "are there warnings".

  "incident"  — Provides RRT (Rapid Response Team) briefs and operational timelines.
                Use for: "incident summary", "what happened", "outage brief",
                "deployment timeline", "post-mortem", "what went wrong".

Rules:
  - Pick exactly the agents needed. Do NOT include agents whose output won't help.
  - For broad ops-health questions ("how are we doing", "operations summary"),
    use all four: ["research", "insight", "alert", "incident"].
  - For incident investigation, combine "incident" + "research".
  - For simple factual lookups, use only "research".
  - Never include more than 4 agents.

Output JSON only:
  {"agents": ["agent1", ...], "reasoning": "<one sentence>", "complexity": "simple|complex"}
"""

_VALID_AGENTS = {"research", "insight", "alert", "incident"}


async def supervisor_node(state: AgentState) -> dict:
    """Classify the user's intent and select which agents to activate."""
    # ── Input guardrails ──────────────────────────────────────────────────────
    guard = check_input(state["question"])
    if not guard.allowed:
        logger.warning("supervisor: input blocked — %s", guard.blocked_reason)
        return {
            "next_agents":  [],
            "model_tier":   "mini",
            "agent_trace":  ["supervisor→blocked"],
            "answer":       guard.blocked_reason,
            "messages":     [AIMessage(content=guard.blocked_reason)],
        }
    # Replace with (possibly truncated) sanitized version
    state = {**state, "question": guard.sanitized}

    history_ctx = ""
    if state.get("history"):
        last = state["history"][-2:]
        history_ctx = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in last)
        history_ctx = f"\nRecent conversation:\n{history_ctx}\n"

    result = await _llm_json(
        _SUPERVISOR_SYSTEM,
        f"{history_ctx}User question: {state['question']}",
        run_name="supervisor",
    )

    agents = [a for a in result.get("agents", []) if a in _VALID_AGENTS]
    if not agents:
        agents = ["research"]   # safe default

    complexity = result.get("complexity", "complex")
    model_tier = "mini" if (complexity == "simple" and len(agents) == 1) else "full"

    reasoning = result.get("reasoning", "")
    logger.info(
        "supervisor: agents=%s tier=%s reasoning=%r",
        agents, model_tier, reasoning,
    )

    return {
        "next_agents": agents,
        "model_tier":  model_tier,
        "agent_trace": [f"supervisor→{','.join(agents)}"],
        "messages":    [AIMessage(content=f"[Routing to: {', '.join(agents)}]")],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Node: Research Agent
# ══════════════════════════════════════════════════════════════════════════════

_RESEARCH_GENERATE_SYSTEM = """\
You are the OpsLens Research Agent — a specialist in retrieving and synthesising
information from the team's operational data sources (GitHub, Jira, Slack, logs, etc.).

Answer the question using ONLY the retrieved context passages provided.
Use markdown formatting. Cite sources with [N] notation.
If context is insufficient, say so — do not fabricate."""


async def research_node(state: AgentState) -> dict:
    """
    Run hybrid retrieval + generation for the user's question.
    Wraps the existing planner logic directly for maximum reuse.
    """
    from ..services.planner import plan_and_answer

    try:
        answer, plan_state = await plan_and_answer(
            question=state["question"],
            tenant_id=state["tenant_id"],
            allowed_sources=state.get("allowed_sources"),
            history=state.get("history") or [],
        )
        docs = plan_state.retrieved_docs
        sources = [
            {
                "title":       d.metadata.get("title", "") or f"{d.metadata.get('source_type','')}: {d.page_content[:40]}…",
                "url":         d.metadata.get("url", ""),
                "source_type": d.metadata.get("source_type", ""),
                "snippet":     d.page_content[:350],
            }
            for d in docs
        ]
        output: dict[str, Any] = {
            "answer":        answer,
            "sources":       sources,
            "retrieved_docs": docs,
            "model_tier":    plan_state.model_tier,
        }
    except Exception as exc:
        logger.error("research_node failed: %s", exc)
        output = {
            "answer":  f"Research agent encountered an error: {exc}",
            "sources": [],
            "retrieved_docs": [],
        }

    outputs = dict(state.get("agent_outputs") or {})
    outputs["research"] = output
    return {"agent_outputs": outputs, "agent_trace": ["research:done"], "retrieved_docs": output.get("retrieved_docs", [])}


# ══════════════════════════════════════════════════════════════════════════════
# Node: Insight Agent
# ══════════════════════════════════════════════════════════════════════════════

_INSIGHT_AGENT_SYSTEM = """\
You are the OpsLens Insight Agent — a specialist in operational patterns and trends.

Given the user's question and operational insight data, provide a clear analysis.
Focus on: what the pattern means for the business, severity/urgency, recommended action.
Use bullet points for clarity. Be concise but actionable."""


async def insight_node(state: AgentState) -> dict:
    """Fetch recent insights from DB and optionally run on-demand detectors."""
    tenant_id = state["tenant_id"]
    question  = state["question"].lower()

    # Determine which detector types are relevant from the question
    detector_hints = {
        "complaint_spike":     any(w in question for w in ["complaint", "support", "customer", "zendesk", "feedback"]),
        "feature_trend":       any(w in question for w in ["feature", "request", "roadmap", "enhancement"]),
        "release_correlation": any(w in question for w in ["release", "deploy", "regression", "version"]),
        "eng_bottleneck":      any(w in question for w in ["block", "stuck", "bottleneck", "stall", "jira", "sprint"]),
        "churn_risk":          any(w in question for w in ["churn", "renewal", "at-risk", "customer health"]),
    }

    # Fetch stored insights (fast path)
    stored = await get_recent_insights(tenant_id, limit=15)

    # For explicitly mentioned detector types, run on-demand (may surface fresher data)
    on_demand: list[dict] = []
    relevant_types = [name for name, match in detector_hints.items() if match]
    if relevant_types:
        tasks = [
            run_insight_detector_now(tenant_id, dtype)
            for dtype in relevant_types[:2]  # cap at 2 to avoid latency spike
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        on_demand = [r for r in results if isinstance(r, dict) and r]

    all_insights = stored + on_demand

    if not all_insights:
        answer = "No significant operational patterns detected in the current data window."
    else:
        # Ask LLM to synthesise the insights into an answer
        insight_text = json.dumps(all_insights[:12], indent=2)
        answer = await _llm_text(
            _INSIGHT_AGENT_SYSTEM,
            f"Question: {state['question']}\n\nAvailable insights:\n{insight_text}",
            run_name="insight_agent",
        )

    output: dict[str, Any] = {
        "answer":          answer,
        "stored_insights": stored,
        "on_demand":       on_demand,
    }
    outputs = dict(state.get("agent_outputs") or {})
    outputs["insight"] = output
    return {"agent_outputs": outputs, "agent_trace": ["insight:done"]}


# ══════════════════════════════════════════════════════════════════════════════
# Node: Alert Agent
# ══════════════════════════════════════════════════════════════════════════════

_ALERT_AGENT_SYSTEM = """\
You are the OpsLens Alert Agent — a specialist in operational alerts and incidents.

Given the user's question and current alert data, provide a clear status report.
Prioritise critical/high severity alerts. Flag anything requiring immediate attention.
Use markdown tables or bullet points. Be direct and actionable."""


async def alert_node(state: AgentState) -> dict:
    """Fetch active alerts and severity summary for the tenant."""
    tenant_id = state["tenant_id"]

    alerts_task  = get_active_alerts(tenant_id, limit=20)
    summary_task = get_alert_summary(tenant_id, hours=24)
    active_alerts, summary = await asyncio.gather(alerts_task, summary_task)

    if not active_alerts:
        answer = "No active alerts at this time. Alert summary for the last 24h:\n" + json.dumps(summary, indent=2)
    else:
        alert_text = json.dumps({"summary": summary, "active_alerts": active_alerts[:15]}, indent=2)
        answer = await _llm_text(
            _ALERT_AGENT_SYSTEM,
            f"Question: {state['question']}\n\nAlert data:\n{alert_text}",
            run_name="alert_agent",
        )

    output: dict[str, Any] = {
        "answer":        answer,
        "active_alerts": active_alerts,
        "summary":       summary,
    }
    outputs = dict(state.get("agent_outputs") or {})
    outputs["alert"] = output
    return {"agent_outputs": outputs, "agent_trace": ["alert:done"]}


# ══════════════════════════════════════════════════════════════════════════════
# Node: Incident Agent
# ══════════════════════════════════════════════════════════════════════════════

_INCIDENT_AGENT_SYSTEM = """\
You are the OpsLens Incident Agent — a specialist in incident response and post-mortems.

Given the user's question and incident/timeline data, synthesise a clear incident report.
Include: what happened, timeline of events, impact, suspected cause, next actions.
Reference specific events from the timeline data. Be precise with timestamps."""


async def incident_node(state: AgentState) -> dict:
    """Fetch RRT briefs and deployment timeline for incident context."""
    tenant_id = state["tenant_id"]

    briefs_task   = get_recent_rrt_briefs(tenant_id, limit=5)
    timeline_task = get_incident_timeline(tenant_id, hours=48)
    briefs, timeline = await asyncio.gather(briefs_task, timeline_task)

    if not briefs and not timeline:
        answer = "No recent incidents or timeline events found."
    else:
        context = json.dumps({"rrt_briefs": briefs, "timeline_events": timeline[:30]}, indent=2)
        answer = await _llm_text(
            _INCIDENT_AGENT_SYSTEM,
            f"Question: {state['question']}\n\nIncident data:\n{context}",
            run_name="incident_agent",
        )

    output: dict[str, Any] = {
        "answer":   answer,
        "briefs":   briefs,
        "timeline": timeline,
    }
    outputs = dict(state.get("agent_outputs") or {})
    outputs["incident"] = output
    return {"agent_outputs": outputs, "agent_trace": ["incident:done"]}


# ══════════════════════════════════════════════════════════════════════════════
# Node: Dispatcher (runs selected agents in parallel)
# ══════════════════════════════════════════════════════════════════════════════

_AGENT_NODES = {
    "research": research_node,
    "insight":  insight_node,
    "alert":    alert_node,
    "incident": incident_node,
}


async def dispatcher_node(state: AgentState) -> dict:
    """Run all selected agents concurrently and merge their outputs into state."""
    agents = state.get("next_agents") or ["research"]
    fns    = [_AGENT_NODES[a] for a in agents if a in _AGENT_NODES]

    if not fns:
        return {}

    results = await asyncio.gather(*[fn(state) for fn in fns], return_exceptions=True)

    merged_outputs: dict[str, Any] = dict(state.get("agent_outputs") or {})
    merged_trace: list[str] = list(state.get("agent_trace") or [])
    merged_docs = list(state.get("retrieved_docs") or [])

    for result in results:
        if isinstance(result, Exception):
            logger.error("dispatcher: agent raised %s", result)
            continue
        merged_outputs.update(result.get("agent_outputs") or {})
        merged_trace.extend(result.get("agent_trace") or [])
        if result.get("retrieved_docs"):
            merged_docs.extend(result["retrieved_docs"])

    return {
        "agent_outputs": merged_outputs,
        "agent_trace":   merged_trace,
        "retrieved_docs": merged_docs,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Node: Synthesizer
# ══════════════════════════════════════════════════════════════════════════════

_SYNTHESIZER_SYSTEM = """\
You are the OpsLens AI final synthesiser.

You receive outputs from one or more specialized agents (Research, Insight, Alert, Incident).
Your job is to combine them into a single, coherent, well-structured response to the user's question.

Rules:
- Do NOT repeat the same information from multiple agents verbatim.
- Integrate findings into a unified narrative.
- Prioritise the most critical/actionable information.
- Use markdown formatting (headers if multiple sections, bullets for lists).
- Keep citations from the Research agent ([N] notation).
- If only one agent ran, you may pass through its answer with minimal editing."""


async def synthesizer_node(state: AgentState) -> dict:
    """Merge multi-agent outputs into a final validated answer."""
    outputs = state.get("agent_outputs") or {}
    question = state["question"]

    if not outputs:
        return {"answer": "No agent produced an output.", "sources": []}

    # If only one agent ran, use its answer directly (no overhead)
    if len(outputs) == 1:
        only = next(iter(outputs.values()))
        answer   = only.get("answer", "")
        sources  = only.get("sources", [])
    else:
        # Build a combined context for the synthesizer
        sections: list[str] = []
        for agent_name, out in outputs.items():
            ans = out.get("answer", "")
            if ans:
                sections.append(f"### {agent_name.title()} Agent\n{ans}")

        combined = "\n\n".join(sections)
        answer = await _llm_text(
            _SYNTHESIZER_SYSTEM,
            f"Question: {question}\n\nAgent outputs:\n{combined}",
            run_name="synthesizer",
        )
        sources = outputs.get("research", {}).get("sources", [])

    # Run validation on the final answer (non-blocking — catch all errors)
    try:
        from ..services.validator import validate_answer
        docs = state.get("retrieved_docs") or []
        validation = await validate_answer(question, docs, answer)
        if not validation.passed:
            disclaimer = validation.disclaimer()
            if disclaimer:
                answer = f"{disclaimer}\n\n{answer}"
    except Exception as exc:
        logger.warning("synthesizer: validation skipped (%s)", exc)

    # ── Output guardrails — PII redaction + content filter ────────────────────
    out_guard = check_output(answer)
    if not out_guard.allowed:
        logger.warning("synthesizer: output blocked — %s", out_guard.blocked_reason)
        answer = out_guard.blocked_reason
    else:
        answer = out_guard.sanitized  # PII-redacted version

    return {
        "answer":  answer,
        "sources": sources,
        "messages": [AIMessage(content=answer)],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Graph assembly
# ══════════════════════════════════════════════════════════════════════════════

def _build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("supervisor",  supervisor_node)
    g.add_node("dispatcher",  dispatcher_node)
    g.add_node("synthesizer", synthesizer_node)

    g.add_edge(START,        "supervisor")
    g.add_edge("supervisor", "dispatcher")
    g.add_edge("dispatcher", "synthesizer")
    g.add_edge("synthesizer", END)

    return g


_compiled_graph = _build_graph().compile()


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

class AgentGraph:
    """Thin wrapper around the compiled LangGraph for dependency injection."""

    async def run(
        self,
        question: str,
        tenant_id: str,
        history: list[dict] | None = None,
        allowed_sources: list[dict] | None = None,
    ) -> tuple[str, list[dict], list[str]]:
        """
        Run the full multi-agent graph.

        Returns:
            (answer, sources, agent_trace)
        """
        initial: AgentState = {
            "messages":       [HumanMessage(content=question)],
            "question":       question,
            "tenant_id":      tenant_id,
            "history":        history or [],
            "allowed_sources": allowed_sources,
            "next_agents":    [],
            "agent_trace":    [],
            "agent_outputs":  {},
            "retrieved_docs": [],
            "answer":         "",
            "sources":        [],
            "model_tier":     "full",
            "iteration":      0,
        }
        final = await _compiled_graph.ainvoke(initial)
        return (
            final.get("answer", ""),
            final.get("sources", []),
            final.get("agent_trace", []),
        )

    async def stream(
        self,
        question: str,
        tenant_id: str,
        company_name: str = "",
        history: list[dict] | None = None,
        allowed_sources: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        """
        Run the agent graph and yield SSE-compatible events.

        Yields:
            {"type": "agent",   "data": "<agent_name>"}          — agent started
            {"type": "token",   "data": "<text>"}                 — answer fragment
            {"type": "sources", "data": [<source dicts>]}
            {"type": "trace",   "data": [<trace entries>]}
            {"type": "done",    "latency_ms": <int>}
            {"type": "error",   "message": "<str>"}
        """
        import time
        start = time.monotonic()

        try:
            # Emit routing event as the graph runs
            answer, sources, trace = await self.run(
                question=question,
                tenant_id=tenant_id,
                history=history,
                allowed_sources=allowed_sources,
            )

            # Emit which agents ran
            agents_used = [t.split(":")[0] for t in trace if ":done" in t]
            for agent_name in agents_used:
                yield {"type": "agent", "data": agent_name}

            # Stream answer word-by-word (token simulation)
            words = answer.split(" ")
            for i, word in enumerate(words):
                yield {"type": "token", "data": word + (" " if i < len(words) - 1 else "")}
                await asyncio.sleep(0)

            yield {"type": "sources", "data": sources}
            yield {"type": "trace",   "data": trace}
            yield {
                "type":       "done",
                "latency_ms": int((time.monotonic() - start) * 1000),
                "agents":     agents_used,
            }

        except Exception as exc:
            logger.exception("AgentGraph.stream failed")
            yield {"type": "error", "message": str(exc)}


# Module-level singleton for dependency injection
_agent_graph = AgentGraph()


def get_agent_graph() -> AgentGraph:
    return _agent_graph


async def run_agent_graph(
    question: str,
    tenant_id: str,
    history: list[dict] | None = None,
    allowed_sources: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Convenience function: run the graph and return (answer, sources)."""
    answer, sources, _ = await _agent_graph.run(
        question=question,
        tenant_id=tenant_id,
        history=history,
        allowed_sources=allowed_sources,
    )
    return answer, sources
