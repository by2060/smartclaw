from __future__ import annotations

import pytest

from flocks.agent.agent import AgentInfo
from flocks.agent.controls import (
    agent_allows_skill,
    agent_allows_subagent,
    filter_agent_skills,
    filter_agent_tools,
)


@pytest.mark.asyncio
async def test_empty_runtime_controls_are_unrestricted(monkeypatch):
    parent = AgentInfo(name="parent", mode="primary", delegatable=False)

    async def fake_get(name: str):
        return parent if name == "parent" else AgentInfo(name=name, mode="subagent")

    from flocks.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_subagent("parent", "any-agent") is True
    assert await agent_allows_skill("parent", "any-skill") is True


@pytest.mark.asyncio
async def test_explicit_empty_sub_agents_denies_all_delegation(monkeypatch):
    parent = AgentInfo(name="parent", mode="primary", delegatable=False, sub_agents=[])

    async def fake_get(name: str):
        return parent if name == "parent" else AgentInfo(name=name, mode="subagent")

    from flocks.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_subagent("parent", "any-agent") is False


@pytest.mark.asyncio
async def test_explicit_empty_skills_denies_all_skills(monkeypatch):
    parent = AgentInfo(name="parent", mode="primary", delegatable=False, skills=[])

    async def fake_get(name: str):
        return parent if name == "parent" else None

    from flocks.agent.registry import Agent

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

    from flocks.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_subagent("parent", "agent1") is True
    assert await agent_allows_subagent("parent", "agent2") is False
    assert await agent_allows_skill("parent", "skill1") is True
    assert await agent_allows_skill("parent", "skill2") is False


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
        type("Tool", (), {"name": "read"})(),
        type("Tool", (), {"name": "websearch"})(),
    ]

    assert [tool.name for tool in filter_agent_tools(agent, tools)] == ["grep", "read"]


def test_filter_agent_tools_tool_search_grants_full_catalog():
    agent = AgentInfo(name="parent", mode="primary", tools=["tool_search"])
    tools = [
        type("Tool", (), {"name": "read"})(),
        type("Tool", (), {"name": "websearch"})(),
    ]

    assert filter_agent_tools(agent, tools) == tools
