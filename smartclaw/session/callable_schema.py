"""
Callable schema resolution for a session.

This module turns the current session callable tool set into concrete tool infos
and the function schema exposed to the model for the current turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from smartclaw.tool.catalog import get_always_load_tool_names
from smartclaw.session.callable_state import (
    get_session_callable_agent,
    get_session_callable_base_tools,
    get_session_callable_tools,
    initialize_session_callable_tools,
)
from smartclaw.tool.registry import ToolRegistry


@dataclass
class CallableSchemaResult:
    tool_infos: List[Any]
    metadata: Dict[str, Any]


def resolve_callable_tool_infos(tool_names: Iterable[str]) -> tuple[List[Any], int]:
    callable_names = set(tool_names)
    tool_infos: List[Any] = []
    enabled_count = 0

    for tool_info in ToolRegistry.list_tools():
        if tool_info.name in {"invalid", "_noop"} or not getattr(tool_info, "enabled", True):
            continue
        enabled_count += 1
        if tool_info.name in callable_names:
            tool_infos.append(tool_info)

    return tool_infos, enabled_count


async def list_session_callable_tool_infos(
    session_id: str,
    declared_tool_names: Optional[Iterable[str]] = None,
    *,
    agent_name: Optional[str] = None,
    step: int = 0,
    event_publish_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
) -> CallableSchemaResult:
    try:
        from smartclaw.agent.controls import titan_session_uses_full_tool_catalog

        use_full_catalog = await titan_session_uses_full_tool_catalog(session_id, agent_name)
    except Exception:
        use_full_catalog = False

    if use_full_catalog:
        enabled_names = {
            tool_info.name
            for tool_info in ToolRegistry.list_tools()
            if tool_info.name not in {"invalid", "_noop"}
            and getattr(tool_info, "enabled", True)
        }
        tool_infos, enabled_count = resolve_callable_tool_infos(enabled_names)
        metadata = {
            "enabledToolCount": enabled_count,
            "callableToolCount": len(enabled_names),
            "alwaysLoadToolCount": len(get_always_load_tool_names()),
            "callableToolNames": sorted(enabled_names),
            "alwaysLoadToolNames": sorted(get_always_load_tool_names()),
            "callableAgent": agent_name,
            "workflowFullToolCatalog": True,
        }
        if event_publish_callback:
            await event_publish_callback("runtime.tool_selection", {
                "sessionID": session_id,
                "step": step,
                **metadata,
            })
        return CallableSchemaResult(tool_infos=tool_infos, metadata=metadata)

    callable_tool_names = await get_session_callable_tools(session_id)
    callable_agent = await get_session_callable_agent(session_id)
    callable_base_tools = await get_session_callable_base_tools(session_id)
    always_load_names = get_always_load_tool_names()
    base_tools = list(declared_tool_names) if declared_tool_names is not None else []
    declared_base = {str(name).strip() for name in base_tools if str(name).strip()}

    if (
        not callable_tool_names
        or (agent_name and callable_agent != agent_name)
        or (declared_tool_names is not None and callable_base_tools != declared_base)
    ):
        callable_tool_names = await initialize_session_callable_tools(
            session_id,
            base_tools,
            always_load_tool_names=always_load_names,
            agent_name=agent_name,
        )
        callable_agent = agent_name

    effective_callable_names = set(callable_tool_names) | always_load_names
    tool_infos, enabled_count = resolve_callable_tool_infos(effective_callable_names)

    metadata = {
        "enabledToolCount": enabled_count,
        "callableToolCount": len(callable_tool_names),
        "alwaysLoadToolCount": len(always_load_names),
        "callableToolNames": sorted(callable_tool_names),
        "alwaysLoadToolNames": sorted(always_load_names),
        "callableAgent": callable_agent,
    }

    if event_publish_callback:
        await event_publish_callback("runtime.tool_selection", {
            "sessionID": session_id,
            "step": step,
            **metadata,
        })

    return CallableSchemaResult(tool_infos=tool_infos, metadata=metadata)
