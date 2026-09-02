"""
Parity checks for Oh-My-SmartClaw integration.
"""

import pytest

from smartclaw.tool import ToolRegistry
from smartclaw.tool.agent.call_omo_agent import call_omo_agent_tool
from smartclaw.tool.registry import ToolContext


class TestOmoToolParity:
    """Minimal parity checks for background tools."""

    def test_background_tools_registered(self):
        tools = ToolRegistry.all_tool_ids()
        assert "background_output" in tools
        assert "background_cancel" in tools


@pytest.mark.asyncio
async def test_call_omo_agent_respects_parent_subagent_allowlist(monkeypatch):
    async def fake_allows(session_id, parent, child):
        return False

    async def fake_allowed_text(session_id, parent):
        return "none"

    monkeypatch.setattr("smartclaw.tool.agent.call_omo_agent.titan_session_allows_subagent", fake_allows)
    monkeypatch.setattr("smartclaw.tool.agent.call_omo_agent.titan_session_allowed_subagents_text", fake_allowed_text)

    ctx = ToolContext(session_id="s", message_id="m", agent="parent")
    result = await call_omo_agent_tool(
        ctx,
        description="explore code",
        prompt="look around",
        subagent_type="explore",
    )

    assert result.success is False
    assert "not allowed to delegate" in (result.error or "")
