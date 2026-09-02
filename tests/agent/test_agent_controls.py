from __future__ import annotations

import pytest

from smartclaw.agent.agent import AgentInfo
from smartclaw.agent.controls import (
    agent_allows_tool,
    agent_allows_skill,
    agent_allows_subagent,
    filter_agent_skills,
    filter_agent_tools,
    titan_session_allows_subagent,
    titan_session_uses_full_skill_catalog,
)


@pytest.mark.asyncio
async def test_empty_runtime_controls_are_unrestricted(monkeypatch):
    parent = AgentInfo(name="parent", mode="primary", delegatable=False)

    async def fake_get(name: str):
        return parent if name == "parent" else AgentInfo(name=name, mode="subagent")

    from smartclaw.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_subagent("parent", "any-agent") is True
    assert await agent_allows_skill("parent", "any-skill") is True


@pytest.mark.asyncio
async def test_explicit_empty_sub_agents_denies_all_delegation(monkeypatch):
    parent = AgentInfo(name="parent", mode="primary", delegatable=False, sub_agents=[])

    async def fake_get(name: str):
        return parent if name == "parent" else AgentInfo(name=name, mode="subagent")

    from smartclaw.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_subagent("parent", "any-agent") is False


@pytest.mark.asyncio
async def test_explicit_empty_skills_denies_all_skills(monkeypatch):
    parent = AgentInfo(name="parent", mode="primary", delegatable=False, skills=[])

    async def fake_get(name: str):
        return parent if name == "parent" else None

    from smartclaw.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_skill("parent", "any-skill") is False


@pytest.mark.asyncio
async def test_runtime_controls_enforce_subagent_and_skill_whitelists(monkeypatch):
    parent = AgentInfo(
        name="parent",
        mode="primary",
        delegatable=False,
        sub_agents=["agent1"],
        skills=["skill1"],
    )
    child = AgentInfo(name="agent1", mode="subagent")

    async def fake_get(name: str):
        return {"parent": parent, "agent1": child}.get(name)

    from smartclaw.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_subagent("parent", "agent1") is True
    assert await agent_allows_subagent("parent", "agent2") is False
    assert await agent_allows_skill("parent", "skill1") is True
    assert await agent_allows_skill("parent", "skill2") is False


@pytest.mark.asyncio
async def test_titan_session_allowed_subagents_scope_delegation(monkeypatch):
    titan = AgentInfo(name="titan", mode="primary", delegatable=False)
    explore = AgentInfo(name="explore", mode="subagent")
    oracle = AgentInfo(name="oracle", mode="subagent")
    session = type(
        "SessionObj",
        (),
        {"user_context": {"allowedSubagents": ["explore"]}},
    )()

    async def fake_get_agent(name: str):
        return {"titan": titan, "explore": explore, "oracle": oracle}.get(name)

    async def fake_get_session(session_id: str):
        return session

    from smartclaw.agent.registry import Agent
    from smartclaw.session.session import Session

    monkeypatch.setattr(Agent, "get", fake_get_agent)
    monkeypatch.setattr(Session, "get_by_id", fake_get_session)

    assert await titan_session_allows_subagent("session-1", "titan", "explore") is True
    assert await titan_session_allows_subagent("session-1", "titan", "oracle") is False


@pytest.mark.asyncio
async def test_workflow_titan_session_allows_only_l1_subagents(monkeypatch):
    titan = AgentInfo(name="titan", mode="primary", delegatable=False)
    l1_agent = AgentInfo(name="l1-agent", mode="subagent")
    l1_agent.agent_type = "L1"
    non_l1 = AgentInfo(name="non-l1", mode="subagent")
    session = type(
        "SessionObj",
        (),
        {
            "category": "workflow",
            "user_context": {"allowedSubagents": ["l1-agent", "non-l1"]},
        },
    )()

    async def fake_get_agent(name: str):
        return {"titan": titan, "l1-agent": l1_agent, "non-l1": non_l1}.get(name)

    async def fake_get_session(session_id: str):
        return session

    from smartclaw.agent.registry import Agent
    from smartclaw.session.session import Session

    monkeypatch.setattr(Agent, "get", fake_get_agent)
    monkeypatch.setattr(Session, "get_by_id", fake_get_session)

    assert await titan_session_allows_subagent("session-1", "titan", "l1-agent") is True
    assert await titan_session_allows_subagent("session-1", "titan", "non-l1") is False


@pytest.mark.asyncio
async def test_workflow_tool_context_allows_only_l1_subagents(monkeypatch):
    titan = AgentInfo(name="titan", mode="primary", delegatable=False)
    l1_agent = AgentInfo(name="l1-agent", mode="subagent")
    l1_agent.agent_type = "L1"
    non_l1 = AgentInfo(name="non-l1", mode="subagent")
    session = type(
        "SessionObj",
        (),
        {
            "category": "user",
            "user_context": {"allowedSubagents": ["l1-agent", "non-l1"]},
        },
    )()

    async def fake_get_agent(name: str):
        return {"titan": titan, "l1-agent": l1_agent, "non-l1": non_l1}.get(name)

    async def fake_get_session(session_id: str):
        return session

    from smartclaw.agent.registry import Agent
    from smartclaw.session.session import Session

    monkeypatch.setattr(Agent, "get", fake_get_agent)
    monkeypatch.setattr(Session, "get_by_id", fake_get_session)

    extra = {"workflow_tool_context": True}
    assert await titan_session_allows_subagent("session-1", "titan", "l1-agent", extra) is True
    assert await titan_session_allows_subagent("session-1", "titan", "non-l1", extra) is False


@pytest.mark.asyncio
async def test_workflow_titan_session_uses_full_skill_catalog(monkeypatch):
    session = type("SessionObj", (), {"category": "workflow"})()

    async def fake_get_session(session_id: str):
        return session

    from smartclaw.session.session import Session

    monkeypatch.setattr(Session, "get_by_id", fake_get_session)

    assert await titan_session_uses_full_skill_catalog("session-1", "titan") is True
    assert await titan_session_uses_full_skill_catalog("session-1", "worker") is False


@pytest.mark.asyncio
async def test_non_titan_ignores_session_allowed_subagents(monkeypatch):
    parent = AgentInfo(name="worker", mode="subagent", sub_agents=["oracle"])
    oracle = AgentInfo(name="oracle", mode="subagent")
    session = type(
        "SessionObj",
        (),
        {"user_context": {"allowedSubagents": ["explore"]}},
    )()

    async def fake_get_agent(name: str):
        return {"worker": parent, "oracle": oracle}.get(name)

    async def fake_get_session(session_id: str):
        return session

    from smartclaw.agent.registry import Agent
    from smartclaw.session.session import Session

    monkeypatch.setattr(Agent, "get", fake_get_agent)
    monkeypatch.setattr(Session, "get_by_id", fake_get_session)

    assert await titan_session_allows_subagent("session-1", "worker", "oracle") is True


def test_filter_agent_skills_preserves_unconfigured_unrestricted_semantics():
    agent = AgentInfo(name="parent", mode="primary", skills=None)
    skills = [
        type("Skill", (), {"name": "skill1"})(),
        type("Skill", (), {"name": "skill2"})(),
    ]

    assert filter_agent_skills(agent, skills) == skills


def test_filter_agent_skills_treats_explicit_empty_as_deny_all():
    agent = AgentInfo(name="parent", mode="primary", skills=[])
    skills = [type("Skill", (), {"name": "skill1"})()]

    assert filter_agent_skills(agent, skills) == []


def test_filter_agent_tools_keeps_only_declared_and_always_load():
    agent = AgentInfo(name="parent", mode="primary", tools=["grep"])
    tools = [
        type("Tool", (), {"name": "grep"})(),
        type("Tool", (), {"name": "question"})(),
        type("Tool", (), {"name": "websearch"})(),
    ]

    assert [tool.name for tool in filter_agent_tools(agent, tools)] == ["grep", "question"]


def test_filter_agent_tools_tool_search_does_not_grant_full_catalog():
    agent = AgentInfo(name="parent", mode="primary", tools=["tool_search"])
    tools = [
        type("Tool", (), {"name": "tool_search"})(),
        type("Tool", (), {"name": "question"})(),
        type("Tool", (), {"name": "read"})(),
        type("Tool", (), {"name": "websearch"})(),
    ]

    assert [tool.name for tool in filter_agent_tools(agent, tools)] == ["tool_search", "question"]


@pytest.mark.asyncio
async def test_agent_allows_tool_search_only_as_declared_tool(monkeypatch):
    parent = AgentInfo(name="parent", mode="primary", tools=["tool_search"])

    async def fake_get(name: str):
        return parent if name == "parent" else None

    from smartclaw.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_tool("parent", "tool_search") is True
    assert await agent_allows_tool("parent", "websearch") is False
    assert await agent_allows_tool("parent", "question") is True
