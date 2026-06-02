"""
OpsLens AI — Multi-Agent System
=================================
Supervisor-based multi-agent architecture built on LangGraph.

Agents:
  - Supervisor    : Routes queries to the right specialist(s)
  - Research      : RAG retrieval + document Q&A (wraps existing planner)
  - Insight       : Pattern detection, trend analysis, operational health
  - Alert         : Alert status, active incidents, severity summaries
  - Incident      : RRT briefs, timelines, post-mortems

Entry point: run_agent_graph(question, tenant_id, ...) → AsyncIterator[dict]
"""
from .graph import run_agent_graph, AgentGraph

__all__ = ["run_agent_graph", "AgentGraph"]
