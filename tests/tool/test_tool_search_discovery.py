from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from smartclaw.tool.registry import ToolCategory, ToolInfo
from smartclaw.tool.system.tool_search import tool_search


def _tool(name: str, category: ToolCategory, native: bool = True) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} description",
        category=category,
        native=native,
        enabled=True,
    )


def _allow_tools(monkeypatch: pytest.MonkeyPatch, tool_names: set[str]) -> None:
    async def allowed_tools(agent_name: str | None) -> list[str]:
        return sorted(tool_names)

    monkeypatch.setattr("smartclaw.tool.system.tool_search.agent_allowed_tools", allowed_tools)


@pytest.mark.asyncio
async def test_tool_search_adds_matches_to_session_callable_tools_and_emits_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("websearch", ToolCategory.BROWSER),
        _tool("read", ToolCategory.FILE),
        _tool("plugin_only", ToolCategory.CUSTOM, native=False),
    ]
    add_callable = AsyncMock(return_value={"websearch"})
    event_callback = AsyncMock()

    monkeypatch.setattr("smartclaw.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("smartclaw.tool.system.tool_search.add_session_callable_tools", add_callable)
    _allow_tools(monkeypatch, {"websearch", "read", "plugin_only"})

    ctx = SimpleNamespace(session_id="session-3", agent="titan", event_publish_callback=event_callback)
    result = await tool_search(ctx, query="web", limit=5)

    assert result.success is True
    assert result.output["callableToolNames"] == ["websearch"]
    assert result.output["callableToolCount"] == 1
    assert result.output["matches"][0]["name"] == "websearch"
    add_callable.assert_awaited_once_with("session-3", ["websearch"], agent_name="titan")
    event_callback.assert_awaited()


@pytest.mark.asyncio
async def test_tool_search_supports_category_and_tag_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        ToolInfo(
            name="websearch",
            description="Search the web for public information",
            category=ToolCategory.BROWSER,
            native=True,
            enabled=True,
            tags=["web", "research"],
        ),
        ToolInfo(
            name="read",
            description="Read local files",
            category=ToolCategory.FILE,
            native=True,
            enabled=True,
            tags=["code-reading"],
        ),
    ]

    monkeypatch.setattr("smartclaw.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    _allow_tools(monkeypatch, {"websearch", "read"})
    monkeypatch.setattr(
        "smartclaw.tool.system.tool_search.add_session_callable_tools",
        AsyncMock(return_value={"websearch"}),
    )

    ctx = SimpleNamespace(session_id="session-4", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="research", category="browser", limit=5)

    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["matches"][0]["name"] == "websearch"
    assert result.output["matches"][0]["matchedTags"] == ["research"]
    assert result.output["matchedTags"] == ["research"]


@pytest.mark.asyncio
async def test_tool_search_supports_exact_batch_select_and_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("websearch", ToolCategory.BROWSER),
        _tool("webfetch", ToolCategory.BROWSER),
        _tool("read", ToolCategory.FILE),
    ]
    add_callable = AsyncMock(return_value={"websearch", "webfetch"})

    monkeypatch.setattr("smartclaw.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("smartclaw.tool.system.tool_search.add_session_callable_tools", add_callable)
    _allow_tools(monkeypatch, {"websearch", "webfetch", "read"})

    ctx = SimpleNamespace(session_id="session-select", agent="titan", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="select:WebSearchTool,webfetch", limit=5)

    assert result.success is True
    assert result.output["normalizedQuery"] == "websearch webfetch"
    assert result.output["callableToolNames"] == ["webfetch", "websearch"]
    assert [match["name"] for match in result.output["matches"]] == ["websearch", "webfetch"]
    add_callable.assert_awaited_once_with(
        "session-select",
        ["websearch", "webfetch"],
        agent_name="titan",
    )


@pytest.mark.asyncio
async def test_tool_search_returns_user_plugin_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("plugin_memory", ToolCategory.CUSTOM, native=False),
        _tool("read", ToolCategory.FILE),
    ]

    monkeypatch.setattr("smartclaw.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    _allow_tools(monkeypatch, {"plugin_memory"})
    monkeypatch.setattr(
        "smartclaw.tool.system.tool_search.add_session_callable_tools",
        AsyncMock(return_value=set()),
    )

    ctx = SimpleNamespace(session_id="session-plugin", agent="titan", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="plugin_memory", limit=5)

    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["matches"][0]["name"] == "plugin_memory"
    assert result.output["matches"][0]["native"] is False


@pytest.mark.asyncio
async def test_tool_search_adds_matching_tools_to_callable_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("read", ToolCategory.FILE),
        _tool("glob", ToolCategory.SEARCH),
    ]
    add_callable = AsyncMock(return_value={"glob", "read"})

    monkeypatch.setattr("smartclaw.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("smartclaw.tool.system.tool_search.add_session_callable_tools", add_callable)
    _allow_tools(monkeypatch, {"read", "glob"})

    ctx = SimpleNamespace(session_id="session-nondeferred", agent="titan", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="read", limit=5)

    assert result.success is True
    assert result.output["count"] == 1
    assert result.output["matches"][0]["name"] == "read"
    assert result.output["callableToolNames"] == ["read"]
    add_callable.assert_awaited_once_with("session-nondeferred", ["read"], agent_name="titan")


@pytest.mark.asyncio
async def test_tool_search_does_not_return_disabled_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enabled_tool = _tool("read", ToolCategory.FILE)
    disabled_tool = ToolInfo(
        name="disabled_searchable",
        description="disabled searchable tool",
        category=ToolCategory.SEARCH,
        native=True,
        enabled=False,
    )
    add_callable = AsyncMock(return_value=set())

    monkeypatch.setattr(
        "smartclaw.tool.system.tool_search.ToolRegistry.list_tools",
        lambda: [enabled_tool, disabled_tool],
    )
    monkeypatch.setattr("smartclaw.tool.system.tool_search.add_session_callable_tools", add_callable)
    _allow_tools(monkeypatch, {"read", "disabled_searchable"})

    ctx = SimpleNamespace(session_id="session-disabled", agent="titan", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="disabled searchable", limit=5)

    assert result.success is True
    assert result.output["count"] == 0
    assert result.output["matches"] == []
    assert result.output["callableToolNames"] == []
    add_callable.assert_awaited_once_with("session-disabled", [], agent_name="titan")


@pytest.mark.asyncio
async def test_tool_search_hides_tools_outside_agent_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("websearch", ToolCategory.BROWSER),
        _tool("subagent_only", ToolCategory.CUSTOM, native=False),
        _tool("question", ToolCategory.SYSTEM),
    ]
    add_callable = AsyncMock(return_value=set())

    monkeypatch.setattr("smartclaw.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("smartclaw.tool.system.tool_search.add_session_callable_tools", add_callable)
    _allow_tools(monkeypatch, {"websearch"})

    ctx = SimpleNamespace(session_id="session-hidden", agent="titan", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="subagent only", limit=5)

    assert result.success is True
    assert result.output["count"] == 0
    assert result.output["matches"] == []
    assert result.output["callableToolNames"] == []
    add_callable.assert_awaited_once_with("session-hidden", [], agent_name="titan")


@pytest.mark.asyncio
async def test_tool_search_select_cannot_bypass_agent_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("websearch", ToolCategory.BROWSER),
        _tool("unauthorized_mcp_tool", ToolCategory.CUSTOM, native=False),
    ]
    add_callable = AsyncMock(return_value={"websearch"})

    monkeypatch.setattr("smartclaw.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("smartclaw.tool.system.tool_search.add_session_callable_tools", add_callable)
    _allow_tools(monkeypatch, {"websearch"})

    ctx = SimpleNamespace(session_id="session-select-hidden", agent="titan", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="select:websearch,unauthorized_mcp_tool", limit=5)

    assert result.success is True
    assert result.output["callableToolNames"] == ["websearch"]
    assert [match["name"] for match in result.output["matches"]] == ["websearch"]
    assert "unauthorized_mcp_tool" not in result.output["discoveredToolNames"]
    add_callable.assert_awaited_once_with(
        "session-select-hidden",
        ["websearch"],
        agent_name="titan",
    )


@pytest.mark.asyncio
async def test_tool_search_includes_always_load_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        _tool("question", ToolCategory.SYSTEM),
        _tool("websearch", ToolCategory.BROWSER),
    ]
    add_callable = AsyncMock(return_value={"question"})

    monkeypatch.setattr("smartclaw.tool.system.tool_search.ToolRegistry.list_tools", lambda: tools)
    monkeypatch.setattr("smartclaw.tool.system.tool_search.add_session_callable_tools", add_callable)
    _allow_tools(monkeypatch, set())

    ctx = SimpleNamespace(session_id="session-always-load", agent="titan", event_publish_callback=AsyncMock())
    result = await tool_search(ctx, query="question", limit=5)

    assert result.success is True
    assert result.output["callableToolNames"] == ["question"]
    assert [match["name"] for match in result.output["matches"]] == ["question"]


def test_runtime_tool_events_are_recognized() -> None:
    from smartclaw.server.routes.event import is_runtime_event

    assert is_runtime_event("runtime.tool_selection") is True
    assert is_runtime_event("runtime.tool_discovery") is True
