"""
OpsLens AI — Multi-Agent Graph Tests
=======================================
Exercises the LangGraph supervisor -> dispatcher -> synthesizer pipeline
(apps/api/agents/graph.py) end-to-end with mocked LLM calls and mocked
DB-backed tools, so it runs without live Postgres/Qdrant/OpenAI credentials.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from apps.api.agents import graph as g


class FakePlanState:
    retrieved_docs = []
    model_tier = "full"


async def fake_plan_and_answer(question, tenant_id, allowed_sources=None, history=None):
    return "RESEARCH_ANSWER", FakePlanState()


async def fake_llm_text(system, user, model=None):
    # Check synthesizer first: its prompt also names the other agents
    # ("Research, Insight, Alert, Incident"), so a naive substring match
    # on those names would misfire against the synthesizer prompt.
    if system.startswith("You are the OpsLens AI final synthesiser"):
        return "SYNTHESIZED_ANSWER"
    if system.startswith("You are the OpsLens Insight Agent"):
        return "INSIGHT_ANSWER"
    if system.startswith("You are the OpsLens Alert Agent"):
        return "ALERT_ANSWER"
    if system.startswith("You are the OpsLens Incident Agent"):
        return "INCIDENT_ANSWER"
    return "GENERIC_ANSWER"


def make_fake_llm_json(agents: list[str]):
    async def _fake(system, user, model="gpt-4o-mini"):
        return {"agents": agents, "reasoning": "test routing", "complexity": "complex"}
    return _fake


@pytest.fixture(autouse=True)
def mock_agent_tools():
    """Mock every DB-backed tool the specialist agents call, and the LLM helpers."""
    with patch.object(g, "_llm_text", fake_llm_text), \
         patch("apps.api.services.planner.plan_and_answer", fake_plan_and_answer), \
         patch.object(g, "get_recent_insights", AsyncMock(return_value=[])), \
         patch.object(g, "run_insight_detector_now", AsyncMock(return_value=None)), \
         patch.object(g, "get_active_alerts", AsyncMock(return_value=[])), \
         patch.object(g, "get_alert_summary", AsyncMock(return_value={"total": 0})), \
         patch.object(g, "get_recent_rrt_briefs", AsyncMock(return_value=[])), \
         patch.object(g, "get_incident_timeline", AsyncMock(return_value=[])):
        yield


async def run_with_routing(question: str, agents: list[str]):
    with patch.object(g, "_llm_json", make_fake_llm_json(agents)):
        agent_graph = g.AgentGraph()
        return await agent_graph.run(question=question, tenant_id="test-tenant")


# ── Routing ─────────────────────────────────────────────────────────────────

async def test_single_agent_routing_research():
    answer, sources, trace = await run_with_routing("What's blocking the sprint?", ["research"])
    agents_used = [t.split(":")[0] for t in trace if ":done" in t]
    assert agents_used == ["research"]
    assert answer == "RESEARCH_ANSWER"


async def test_single_agent_routing_alert():
    answer, sources, trace = await run_with_routing("Any active critical alerts?", ["alert"])
    agents_used = [t.split(":")[0] for t in trace if ":done" in t]
    assert agents_used == ["alert"]


async def test_single_agent_routing_incident():
    answer, sources, trace = await run_with_routing("What happened during the outage?", ["incident"])
    agents_used = [t.split(":")[0] for t in trace if ":done" in t]
    assert agents_used == ["incident"]


async def test_invalid_agent_names_filtered_with_safe_default():
    """Supervisor output containing agents outside the valid set should fall
    back to the safe default ('research') rather than crash the dispatcher."""
    answer, sources, trace = await run_with_routing("anything", ["not_a_real_agent"])
    agents_used = [t.split(":")[0] for t in trace if ":done" in t]
    assert agents_used == ["research"]


# ── Parallel dispatch ─────────────────────────────────────────────────────

async def test_multi_agent_dispatch_runs_all_selected_agents():
    answer, sources, trace = await run_with_routing(
        "Summarise our ops health", ["research", "insight", "alert", "incident"]
    )
    agents_used = sorted(t.split(":")[0] for t in trace if ":done" in t)
    assert agents_used == ["alert", "incident", "insight", "research"]


async def test_agent_trace_has_no_duplicate_entries():
    """Regression test: dispatcher previously duplicated the supervisor's
    routing line once per concurrent agent (apps/api/agents/graph.py)."""
    answer, sources, trace = await run_with_routing(
        "Summarise our ops health", ["research", "insight", "alert", "incident"]
    )
    assert trace.count("supervisor→research,insight,alert,incident") == 1
    assert len(trace) == len(set(trace)), f"trace has duplicate entries: {trace}"


# ── Synthesis ───────────────────────────────────────────────────────────────

async def test_single_agent_output_passed_through_without_synthesis_llm_call():
    """With exactly one agent output, the synthesizer should use it directly
    rather than paying for an extra LLM call."""
    answer, sources, trace = await run_with_routing("What's blocking the sprint?", ["research"])
    assert answer == "RESEARCH_ANSWER"


async def test_multi_agent_outputs_are_synthesized_into_one_answer():
    answer, sources, trace = await run_with_routing(
        "Summarise our ops health", ["research", "insight", "alert", "incident"]
    )
    assert answer == "SYNTHESIZED_ANSWER"


async def test_dispatcher_survives_one_agent_raising():
    """If one specialist agent raises, the others should still complete and
    the graph should not crash."""
    # _AGENT_NODES holds direct function references captured at module load,
    # so patching g.insight_node alone wouldn't affect dispatch -- patch the
    # dict entry that dispatcher_node actually calls.
    with patch.dict(g._AGENT_NODES, {"insight": AsyncMock(side_effect=RuntimeError("boom"))}):
        answer, sources, trace = await run_with_routing(
            "Summarise our ops health", ["research", "insight", "alert", "incident"]
        )
        agents_used = sorted(t.split(":")[0] for t in trace if ":done" in t)
        assert "insight" not in agents_used
        assert {"research", "alert", "incident"}.issubset(set(agents_used))
        assert answer  # graph still produced an answer from the surviving agents
