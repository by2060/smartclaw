from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import smartclaw.session.runner as runner_module
from smartclaw.session.message import UserMessageInfo
from smartclaw.session.runner import RunnerCallbacks, SessionRunner
from smartclaw.session.session import SessionInfo
from smartclaw.tool.agent import delegate_task as delegate_module
from smartclaw.tool.registry import Tool, ToolContext, ToolInfo, ToolRegistry
from smartclaw.workflow.tools_adapter import SmartClawToolAdapter


def _context() -> ToolContext:
    return ToolContext(session_id="parent-session", message_id="parent-message", agent="titan")


@pytest.fixture(autouse=True)
def _delegation_defaults(monkeypatch):
    monkeypatch.setattr(
        delegate_module,
        "titan_session_uses_full_skill_catalog",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        delegate_module,
        "titan_session_allows_subagent",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        delegate_module,
        "_find_completed_delegate",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        delegate_module.Config,
        "get",
        AsyncMock(return_value=SimpleNamespace(categories=None)),
    )


@pytest.mark.asyncio
async def test_process_step_stops_when_agent_does_not_exist(monkeypatch):
    session = SessionInfo.model_construct(
        id="session-1",
        project_id="project-1",
        directory="/tmp",
        title="test",
    )
    on_error = AsyncMock()
    runner = SessionRunner(session=session, callbacks=RunnerCallbacks(on_error=on_error))
    last_user = UserMessageInfo.model_construct(
        id="message-1",
        sessionID="session-1",
        role="user",
        agent="missing-agent",
        time={"created": 1},
    )
    monkeypatch.setattr(runner_module.Agent, "get", AsyncMock(return_value=None))

    result = await runner._process_step([last_user], last_user)

    assert result.action == "stop"
    assert result.error == 'Agent "missing-agent" not found'
    on_error.assert_awaited_once_with(result.error)


@pytest.mark.asyncio
async def test_agent_lookup_returns_agent_and_handles_registry_error(monkeypatch):
    agent = SimpleNamespace(name="worker")
    lookup = AsyncMock(side_effect=[agent, RuntimeError("registry unavailable")])
    monkeypatch.setattr(delegate_module.Agent, "get", lookup)

    assert await delegate_module._lookup_agent("worker") is agent
    assert await delegate_module._lookup_agent("missing") is None


@pytest.mark.asyncio
async def test_direct_delegation_rejects_missing_agent(monkeypatch):
    monkeypatch.setattr(delegate_module, "_lookup_agent", AsyncMock(return_value=None))

    result = await delegate_module.delegate_task_tool(
        _context(),
        prompt="do work",
        subagent_type="missing-agent",
    )

    assert result.success is False
    assert result.metadata["reason"] == "agent_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolved_name", "expected"),
    [
        ("titan-junior", "Use category parameter instead"),
        ("restricted-agent", "cannot be delegated to"),
    ],
)
async def test_direct_delegation_rejects_non_delegatable_agents(
    monkeypatch,
    resolved_name,
    expected,
):
    target = SimpleNamespace(name=resolved_name, delegatable=False)
    monkeypatch.setattr(delegate_module, "_lookup_agent", AsyncMock(return_value=target))
    monkeypatch.setattr(delegate_module, "is_delegatable", lambda _name: False)

    result = await delegate_module.delegate_task_tool(
        _context(),
        prompt="do work",
        subagent_type=resolved_name,
    )

    assert result.success is False
    assert expected in result.error
    assert result.metadata["reason"] == "agent_not_delegatable"


@pytest.mark.asyncio
async def test_continuation_rejects_session_with_missing_agent(monkeypatch):
    session = SimpleNamespace(id="child", agent="removed-agent")
    monkeypatch.setattr(delegate_module.Session, "get_by_id", AsyncMock(return_value=session))
    monkeypatch.setattr(delegate_module, "_lookup_agent", AsyncMock(return_value=None))

    result = await delegate_module.delegate_task_tool(
        _context(),
        prompt="continue",
        session_id="child",
    )

    assert result.success is False
    assert result.metadata["reason"] == "agent_not_found"
    assert "continuation was not started" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [None, SimpleNamespace(name="titan-junior", delegatable=False)])
async def test_category_rejects_missing_or_non_delegatable_junior(monkeypatch, agent):
    monkeypatch.setattr(delegate_module, "_lookup_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(delegate_module, "is_delegatable", lambda _name: False)

    result = await delegate_module.delegate_task_tool(
        _context(),
        prompt="quick work",
        category="quick",
    )

    assert result.success is False
    assert result.metadata["reason"] in {"agent_not_found", "agent_not_delegatable"}


def test_registry_list_tools_marks_only_hard_disabled_entries(monkeypatch):
    enabled = Tool(ToolInfo(name="enabled", description="enabled"), AsyncMock())
    disabled = Tool(ToolInfo(name="disabled", description="disabled"), AsyncMock())
    monkeypatch.setattr(ToolRegistry, "_initialized", True)
    monkeypatch.setattr(ToolRegistry, "_tools", {"enabled": enabled, "disabled": disabled})
    monkeypatch.setattr(ToolRegistry, "_hard_disabled_tools", {"disabled"})

    infos = ToolRegistry.list_tools()

    assert [info.name for info in infos] == ["enabled", "disabled"]
    assert enabled.info.enabled is True
    assert disabled.info.enabled is False
    assert ToolRegistry.get("disabled").info.enabled is False


def test_workflow_adapter_filters_and_resolves_tool_states(monkeypatch):
    enabled = Tool(ToolInfo(name="enabled", description="enabled"), AsyncMock())
    disabled = Tool(
        ToolInfo(name="disabled", description="disabled", enabled=False),
        AsyncMock(),
    )
    blocked = Tool(ToolInfo(name="run_workflow", description="blocked"), AsyncMock())
    adapter = SmartClawToolAdapter.__new__(SmartClawToolAdapter)
    adapter._ctx = None

    monkeypatch.setattr(ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(
        ToolRegistry,
        "list_tools",
        lambda: [enabled.info, disabled.info, blocked.info],
    )
    monkeypatch.setattr(
        ToolRegistry,
        "get",
        lambda name: {"enabled": enabled, "disabled": disabled}.get(name),
    )

    assert adapter.list() == ["enabled"]
    assert adapter.get("run_workflow") is None
    assert adapter.get("missing") is None
    assert adapter.get("disabled") is None
    assert adapter.get("enabled") is not None
    assert adapter.get_spec("run_workflow") is None
    assert adapter.get_spec("missing") is None
    assert adapter.get_spec("disabled") is None
    assert adapter.get_spec("enabled").name == "enabled"
