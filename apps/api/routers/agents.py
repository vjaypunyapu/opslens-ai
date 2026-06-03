"""
OpsLens AI — Multi-Agent Router
================================
Exposes the multi-agent system's capabilities via REST API.

Endpoints:
    GET  /api/v1/agents/status      — List available agents and their capabilities
    POST /api/v1/agents/run         — Run a single-shot multi-agent query (non-streaming)
    POST /api/v1/agents/stream      — Streaming multi-agent query with agent events
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agents.graph import get_agent_graph
from ..auth.dependencies import TenantContext, require_member
from ..services.permissions import get_allowed_sources
from ..db.session import get_db
from ..utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

# ── Agent capability registry ─────────────────────────────────────────────────
_AGENT_CAPABILITIES = [
    {
        "name":        "supervisor",
        "description": "Routes queries to the right specialist agent(s) based on intent.",
        "triggers":    ["all queries — runs first"],
    },
    {
        "name":        "research",
        "description": "Retrieves and synthesises information from ingested documents, code, tickets, and messages via hybrid RAG.",
        "triggers":    ["factual questions", "what is X", "show me Y", "explain Z", "documents"],
    },
    {
        "name":        "insight",
        "description": "Surfaces operational patterns: complaint spikes, feature trends, release correlations, engineering bottlenecks, churn risk.",
        "triggers":    ["patterns", "trends", "anomalies", "operational health", "feature requests"],
    },
    {
        "name":        "alert",
        "description": "Reports on active alerts, recent alert history, and severity summaries.",
        "triggers":    ["active alerts", "critical incidents", "what's firing", "error rate"],
    },
    {
        "name":        "incident",
        "description": "Provides RRT briefs, deployment timelines, and incident post-mortems.",
        "triggers":    ["incident summary", "what happened", "outage", "deployment timeline"],
    },
]


# ── Request schemas ───────────────────────────────────────────────────────────
class AgentQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    history:  list[dict] | None = Field(default=None, description="Prior chat turns for context")


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.get("/status")
async def get_agent_status(
    ctx: Annotated[TenantContext, Depends(require_member)],
):
    """Return available agents, their capabilities, and routing rules."""
    return {
        "multi_agent": True,
        "agents":      _AGENT_CAPABILITIES,
        "routing":     "LLM-based intent classification (supervisor pattern)",
        "parallelism": "Agents selected by supervisor run concurrently via asyncio.gather",
        "validation":  "Auditor + Gatekeeper + Strategist validate the final synthesised answer",
    }


@router.post("/run")
async def run_agent_query(
    body: AgentQueryRequest,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """
    Run a multi-agent query and return the full result synchronously.
    For streaming use /agents/stream instead.
    """
    allowed_sources = await get_allowed_sources(ctx.user_id, ctx.tenant_id, db)
    graph = get_agent_graph()

    answer, sources, trace = await graph.run(
        question=body.question,
        tenant_id=str(ctx.tenant_uuid),
        history=body.history,
        allowed_sources=allowed_sources,
    )
    agents_used = [t.split(":")[0] for t in trace if ":done" in t]

    return {
        "answer":      answer,
        "sources":     sources,
        "agents_used": agents_used,
        "agent_trace": trace,
    }


@router.post("/stream")
async def stream_agent_query(
    body: AgentQueryRequest,
    ctx: Annotated[TenantContext, Depends(require_member)],
    db=Depends(get_db),
):
    """
    Stream a multi-agent query as Server-Sent Events.

    Event types:
      {"type": "agent",   "data": "<agent_name>"}     — agent activated
      {"type": "token",   "data": "<text fragment>"}   — answer token
      {"type": "sources", "data": [<source dicts>]}
      {"type": "trace",   "data": [<trace entries>]}
      {"type": "done",    "latency_ms": <int>, "agents": [<names>]}
      {"type": "error",   "message": "<str>"}
    """
    allowed_sources = await get_allowed_sources(ctx.user_id, ctx.tenant_id, db)
    graph = get_agent_graph()

    async def event_stream():
        try:
            async for event in graph.stream(
                question=body.question,
                tenant_id=str(ctx.tenant_uuid),
                company_name=ctx.company_name,
                history=body.history,
                allowed_sources=allowed_sources,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.exception("Agent stream error tenant=%s", ctx.tenant_uuid)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
        },
    )
