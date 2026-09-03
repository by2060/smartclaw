import json
from unittest.mock import AsyncMock

import pytest

from smartclaw.tool.registry import ParameterType, ToolContext, ToolInfo, ToolParameter, ToolRegistry
from smartclaw.tool.task import todo as todo_module


def test_todowrite_is_registered_with_structured_array_schema():
    schema = ToolRegistry.get_schema("todowrite")
    assert schema is not None
    assert "todos" in schema.required
    todos = schema.properties["todos"]
    assert todos["type"] == "array"
    assert todos["items"]["type"] == "object"
    assert todos["items"]["properties"]["content"]["type"] == "string"
    assert todos["items"]["properties"]["status"]["type"] == "string"

    tool = ToolRegistry.get("todowrite")
    assert tool is not None
    description = tool.info.parameters[0].description
    assert "Array item type must be a dict" in description
    assert isinstance(description, str)
    assert not description.startswith("(")


def test_tool_info_preserves_custom_json_schema_and_merges_metadata():
    parameter = ToolParameter(
        name="items",
        type=ParameterType.ARRAY,
        description="item list",
        required=True,
        default=["default"],
        enum=["a", "b"],
        json_schema={"type": "array", "items": {"type": "object"}},
    )
    schema = ToolInfo(name="x", description="x", parameters=[parameter]).get_schema().to_json_schema()
    assert schema["properties"]["items"] == {
        "type": "array",
        "items": {"type": "object"},
        "description": "item list",
        "default": ["default"],
        "enum": ["a", "b"],
    }
    assert schema["required"] == ["items"]


@pytest.mark.asyncio
async def test_todowrite_normalizes_invalid_items_and_statuses():
    todo_module._todo_storage.clear()
    ctx = ToolContext(
        session_id="todo-schema-test",
        message_id="message",
        permission_callback=AsyncMock(),
    )
    result = await todo_module.todowrite_tool(
        ctx,
        todos=[
            {"id": 7, "content": "done", "status": "completed"},
            {"content": 123, "status": "unexpected"},
            "ignore-me",
        ],
    )
    assert result.success is True
    normalized = json.loads(result.output)
    assert normalized == [
        {"id": "7", "content": "done", "status": "completed"},
        {"id": "2", "content": "123", "status": "pending"},
    ]
    assert result.title == "1 todos"
    assert todo_module.get_todos(ctx.session_id) == normalized


@pytest.mark.asyncio
async def test_todoread_returns_empty_and_existing_session_todos():
    todo_module._todo_storage.clear()
    ctx = ToolContext(
        session_id="todo-read-test",
        message_id="message",
        permission_callback=AsyncMock(),
    )
    empty = await todo_module.todoread_tool(ctx)
    assert empty.success is True
    assert json.loads(empty.output) == []
    assert empty.title == "0 todos"

    todo_module.set_todos(ctx.session_id, [{"id": "1", "content": "done", "status": "completed"}])
    result = await todo_module.todoread_tool(ctx)
    assert json.loads(result.output) == todo_module.get_todos(ctx.session_id)
    assert result.title == "0 todos"

