from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_callable_tools_reinitialize_when_agent_changes(tmp_path):
    from flocks.config.config import Config
    from flocks.session.callable_schema import list_session_callable_tool_infos
    from flocks.session.callable_state import get_session_callable_agent, get_session_callable_tools
    from flocks.storage.storage import Storage

    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    try:
        await Storage.init(tmp_path / "flocks.db")

        await list_session_callable_tool_infos(
            "session-agent-switch",
            declared_tool_names=["read"],
            agent_name="agent-a",
        )
        assert await get_session_callable_agent("session-agent-switch") == "agent-a"
        assert "read" in await get_session_callable_tools("session-agent-switch")

        await list_session_callable_tool_infos(
            "session-agent-switch",
            declared_tool_names=["write"],
            agent_name="agent-b",
        )
        assert await get_session_callable_agent("session-agent-switch") == "agent-b"
        tools = await get_session_callable_tools("session-agent-switch")
        assert "write" in tools
        assert "read" not in tools

        await list_session_callable_tool_infos(
            "session-agent-switch",
            declared_tool_names=["skill"],
            agent_name="agent-b",
        )
        tools = await get_session_callable_tools("session-agent-switch")
        assert "skill" in tools
        assert "write" not in tools
    finally:
        from flocks.session import callable_state

        callable_state._cache.clear()
        callable_state._agent_cache.clear()
        callable_state._base_cache.clear()
        Config._global_config = None
        Config._cached_config = None
        Storage._db_path = None
        Storage._initialized = False
