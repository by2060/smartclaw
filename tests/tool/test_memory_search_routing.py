from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from smartclaw.tool.registry import ToolContext, ToolRegistry


def _memory_module():
    return importlib.import_module("smartclaw.tool.system.memory")


def test_memory_search_description_excludes_public_person_queries():
    module = _memory_module()
    tool = ToolRegistry.get("memory_search")

    assert tool is not None
    assert tool.info.description == module.MEMORY_SEARCH_DESCRIPTION
    assert "current human user's remembered profile" in tool.info.description
    assert "Do not use it for public people" in tool.info.description
    assert "X是谁" in tool.info.description


@pytest.mark.asyncio
async def test_empty_memory_search_result_instructs_public_web_fallback(monkeypatch):
    module = _memory_module()

    class EmptyMemory:
        async def search(self, **kwargs):
            return []

    async def get_empty_memory(ctx):
        return EmptyMemory(), None

    monkeypatch.setattr(module, "_get_session_memory", get_empty_memory)

    result = await module.memory_search_tool(
        ToolContext(
            session_id="ses_public_person",
            message_id="msg_public_person",
            agent="titan",
            call_id="call_public_person",
        ),
        query="轩晓荷",
    )

    assert result.success is True
    assert result.output["count"] == 0
    assert result.output["public_information_fallback"] == module.EMPTY_MEMORY_PUBLIC_FALLBACK
    assert "call websearch before responding" in result.output["public_information_fallback"]


@pytest.mark.asyncio
async def test_nonempty_memory_search_result_does_not_add_public_fallback(monkeypatch):
    module = _memory_module()
    memory_result = SimpleNamespace(
        path="memory.md",
        start_line=1,
        end_line=2,
        score=0.9,
        snippet="remembered fact",
        source=SimpleNamespace(value="memory"),
        citation="memory.md:1-2",
    )

    class NonEmptyMemory:
        async def search(self, **_kwargs):
            return [memory_result]

    async def get_memory(_ctx):
        return NonEmptyMemory(), None

    monkeypatch.setattr(module, "_get_session_memory", get_memory)

    result = await module.memory_search_tool(
        ToolContext(session_id="session-1", message_id="message-1", agent="titan"),
        query="remembered fact",
    )

    assert result.success is True
    assert result.output["count"] == 1
    assert "public_information_fallback" not in result.output
