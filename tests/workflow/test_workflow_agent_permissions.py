from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from flocks.agent.agent import AgentInfo
from flocks.agent.controls import agent_allows_workflow_execution, filter_workflow_entries_for_agent
from flocks.tool import ToolContext
from flocks.tool.task.run_workflow import run_workflow_tool
from flocks.tool.task.run_workflow_node import run_workflow_node_tool


@pytest.mark.asyncio
async def test_agent_workflow_execution_denies_missing_and_empty_workflows(monkeypatch):
    missing = AgentInfo(name="missing", mode="primary", workflows=None)
    empty = AgentInfo(name="empty", mode="primary", workflows=[])

    async def fake_get(name: str):
        return {"missing": missing, "empty": empty}.get(name)

    from flocks.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_workflow_execution("missing", workflow_id="alert_triage") is False
    assert await agent_allows_workflow_execution("empty", workflow_id="alert_triage") is False


@pytest.mark.asyncio
async def test_agent_workflow_execution_allows_declared_workflow(monkeypatch):
    agent = AgentInfo(
        name="worker",
        mode="subagent",
        workflows=["alert_triage"],
    )

    async def fake_get(name: str):
        return agent if name == "worker" else None

    from flocks.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_workflow_execution("worker", workflow_id="alert_triage") is True
    assert await agent_allows_workflow_execution(
        "worker",
        workflow_path=".flocks/plugins/workflows/alert_triage/workflow.json",
    ) is True
    assert await agent_allows_workflow_execution(
        "worker",
        workflow_path=".flocks/plugins/workflows/alert_triage",
    ) is False
    assert await agent_allows_workflow_execution(
        "worker",
        workflow_path="nested/path/workflow.json",
    ) is False
    assert await agent_allows_workflow_execution("worker", workflow_id="other") is False
    assert await agent_allows_workflow_execution(
        "worker",
        workflow_id=".flocks/plugins/workflows/alert_triage/workflow.json",
    ) is True


@pytest.mark.asyncio
async def test_agent_workflow_execution_list_token_does_not_allow_run(monkeypatch):
    agent = AgentInfo(name="viewer", mode="subagent", workflows=["workflow:list"])

    async def fake_get(name: str):
        return agent if name == "viewer" else None

    from flocks.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    assert await agent_allows_workflow_execution("viewer", workflow_id="alert_triage") is False


@pytest.mark.asyncio
async def test_rex_workflow_session_bypasses_workflow_execution_grants(monkeypatch):
    session = type("SessionObj", (), {"category": "workflow"})()

    async def fake_get_session(session_id: str):
        return session

    from flocks.session.session import Session

    monkeypatch.setattr(Session, "get_by_id", fake_get_session)

    assert await agent_allows_workflow_execution(
        "rex",
        workflow_id="not-in-agent-yaml",
        session_id="workflow-session",
    ) is True




@pytest.mark.asyncio
async def test_agent_workflow_catalog_filters_to_declared_ids(monkeypatch):
    agent = AgentInfo(name="worker", mode="subagent", workflows=["visible-workflow"])

    async def fake_get(name: str):
        return agent if name == "worker" else None

    from flocks.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    entries = [
        {"id": "visible-workflow", "name": "Visible"},
        {"id": "hidden-workflow", "name": "Hidden"},
    ]

    visible = await filter_workflow_entries_for_agent(entries, "worker")

    assert [entry["id"] for entry in visible] == ["visible-workflow"]


@pytest.mark.asyncio
async def test_rex_workflow_session_bypasses_workflow_catalog_filter(monkeypatch):
    rex = AgentInfo(name="rex", mode="primary", workflows=[])
    session = type("SessionObj", (), {"category": "workflow"})()

    async def fake_get(name: str):
        return rex if name == "rex" else None

    async def fake_get_session(session_id: str):
        return session

    from flocks.agent.registry import Agent
    from flocks.session.session import Session

    monkeypatch.setattr(Agent, "get", fake_get)
    monkeypatch.setattr(Session, "get_by_id", fake_get_session)

    entries = [
        {"id": "one", "name": "One"},
        {"id": "two", "name": "Two"},
    ]

    visible = await filter_workflow_entries_for_agent(entries, "rex", session_id="workflow-session")

    assert visible == entries


@pytest.mark.asyncio
async def test_workflow_route_catalog_default_management_view_keeps_all(monkeypatch):
    from flocks.server.routes.workflow import _filter_workflow_items_for_request
    from flocks.agent.registry import Agent

    async def fail_get(name: str):
        raise AssertionError("management catalog view must not resolve agent permissions")

    monkeypatch.setattr(Agent, "get", fail_get)
    items = [
        {"id": "one", "name": "One"},
        {"id": "two", "name": "Two"},
    ]

    visible = await _filter_workflow_items_for_request(items, agent=None, session_id=None, scope=None)

    assert visible == items


@pytest.mark.asyncio
async def test_workflow_route_agent_scope_empty_agent_defaults_to_rex(monkeypatch):
    from flocks.agent.registry import Agent
    from flocks.server.routes.workflow import _filter_workflow_items_for_request

    rex = AgentInfo(name="rex", mode="primary", workflows=[])

    async def fake_get(name: str):
        return rex if name == "rex" else None

    monkeypatch.setattr(Agent, "get", fake_get)
    items = [{"id": "one", "name": "One"}]

    visible = await _filter_workflow_items_for_request(items, agent=None, session_id=None, scope="agent")

    assert visible == []


@pytest.mark.asyncio
async def test_workflow_route_agent_scope_filters_to_declared_ids(monkeypatch):
    from flocks.agent.registry import Agent
    from flocks.server.routes.workflow import _filter_workflow_items_for_request

    agent = AgentInfo(name="limited", mode="primary", workflows=["allowed"])

    async def fake_get(name: str):
        return agent if name == "limited" else None

    monkeypatch.setattr(Agent, "get", fake_get)
    items = [
        {"id": "allowed", "name": "Allowed"},
        {"id": "blocked", "name": "Blocked"},
    ]

    visible = await _filter_workflow_items_for_request(items, agent="limited", session_id=None, scope=None)

    assert [item["id"] for item in visible] == ["allowed"]

@pytest.mark.asyncio
async def test_run_workflow_tool_blocks_agent_without_workflow_grant():
    workflow = {
        "id": "blocked-workflow",
        "name": "Blocked Workflow",
        "metadata": {},
        "nodes": [{"id": "node-1", "type": "python", "code": "result = {}"}],
        "edges": [],
    }
    ctx = ToolContext(session_id="user-session", message_id="msg", agent="worker")
    run_fn = Mock(name="run_workflow")

    with patch(
        "flocks.tool.task.run_workflow._get_workflow_runtime",
        return_value=(Mock(name="RequirementsInstaller"), run_fn, object),
    ):
        result = await run_workflow_tool(ctx=ctx, workflow=workflow)

    assert result.success is False
    assert result.metadata["blocked_by_agent_workflows"] is True
    run_fn.assert_not_called()


@pytest.mark.asyncio
async def test_run_workflow_tool_blocks_forged_inline_workflow_id(monkeypatch):
    agent = AgentInfo(name="worker", mode="subagent", workflows=["allowed-workflow"])

    async def fake_get(name: str):
        return agent if name == "worker" else None

    from flocks.agent.registry import Agent

    monkeypatch.setattr(Agent, "get", fake_get)

    workflow = {
        "id": "allowed-workflow",
        "name": "allowed-workflow",
        "metadata": {},
        "nodes": [{"id": "node-1", "type": "python", "code": "result = {}"}],
        "edges": [],
    }
    ctx = ToolContext(session_id="user-session", message_id="msg", agent="worker")
    run_fn = Mock(name="run_workflow")

    with patch(
        "flocks.tool.task.run_workflow._get_workflow_runtime",
        return_value=(Mock(name="RequirementsInstaller"), run_fn, object),
    ):
        result = await run_workflow_tool(ctx=ctx, workflow=workflow)

    assert result.success is False
    assert result.metadata["blocked_by_agent_workflows"] is True
    run_fn.assert_not_called()


@pytest.mark.asyncio
async def test_run_workflow_node_tool_blocks_agent_without_workflow_grant():
    workflow = {
        "id": "blocked-node-workflow",
        "name": "Blocked Node Workflow",
        "metadata": {},
        "nodes": [{"id": "node-1", "type": "python", "code": "result = {}"}],
        "edges": [],
    }
    ctx = ToolContext(session_id="user-session", message_id="msg", agent="worker")

    result = await run_workflow_node_tool(ctx=ctx, workflow=workflow, node_id="node-1")

    assert result.success is False
    assert result.metadata["blocked_by_agent_workflows"] is True
