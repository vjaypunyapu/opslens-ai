"""
OpsLens AI — Multi-Agent State
================================
Shared state that flows through the LangGraph agent graph.
Every node reads from and writes to this TypedDict.
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # ── Conversation ──────────────────────────────────────────────────────────
    messages:        Annotated[list[BaseMessage], add_messages]
    question:        str
    history:         list[dict]          # prior chat turns for context

    # ── Tenant / auth ─────────────────────────────────────────────────────────
    tenant_id:       str
    allowed_sources: list[dict] | None   # None = admin; [] = no access

    # ── Routing ───────────────────────────────────────────────────────────────
    next_agents:     list[str]           # agents selected by supervisor
    agent_trace:     list[str]           # audit trail of executed agents

    # ── Agent outputs ─────────────────────────────────────────────────────────
    agent_outputs:   dict[str, Any]      # keyed by agent name

    # ── Research agent ────────────────────────────────────────────────────────
    retrieved_docs:  list[Document]
    model_tier:      str                 # "mini" | "full"

    # ── Final response ────────────────────────────────────────────────────────
    answer:          str
    sources:         list[dict]
    iteration:       int
