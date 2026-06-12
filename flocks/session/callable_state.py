"""
Session-scoped callable tool storage.

This is the single runtime source of truth for which tools are callable within
the current session.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Set

from flocks.storage.storage import Storage


_CALLABLE_PREFIX = "session_callable_tools:"
_cache: Dict[str, Set[str]] = {}
_agent_cache: Dict[str, Optional[str]] = {}
_base_cache: Dict[str, Set[str]] = {}


def _normalize_tool_names(tool_names: Iterable[str]) -> Set[str]:
    return {
        str(name).strip()
        for name in tool_names
        if str(name).strip()
    }


async def get_session_callable_tools(session_id: str) -> Set[str]:
    if session_id in _cache:
        return set(_cache[session_id])

    stored = await Storage.get(f"{_CALLABLE_PREFIX}{session_id}")
    if isinstance(stored, dict):
        names = set(str(name) for name in stored.get("tools", []) if name)
        agent_name = stored.get("agent")
        base_names = set(str(name) for name in stored.get("base_tools", []) if name)
    elif isinstance(stored, list):
        names = set(str(name) for name in stored if name)
        agent_name = None
        base_names = set()
    else:
        names = set()
        agent_name = None
        base_names = set()

    _cache[session_id] = set(names)
    _agent_cache[session_id] = str(agent_name) if agent_name else None
    _base_cache[session_id] = set(base_names)
    return set(names)


async def get_session_callable_agent(session_id: str) -> Optional[str]:
    if session_id not in _cache:
        await get_session_callable_tools(session_id)
    return _agent_cache.get(session_id)


async def get_session_callable_base_tools(session_id: str) -> Set[str]:
    if session_id not in _cache:
        await get_session_callable_tools(session_id)
    return set(_base_cache.get(session_id, set()))


async def set_session_callable_tools(
    session_id: str,
    tool_names: Iterable[str],
    *,
    agent_name: Optional[str] = None,
    base_tool_names: Optional[Iterable[str]] = None,
) -> Set[str]:
    normalized = set(sorted(_normalize_tool_names(tool_names)))
    normalized_base = set(sorted(_normalize_tool_names(base_tool_names or [])))
    _cache[session_id] = normalized
    _agent_cache[session_id] = agent_name
    _base_cache[session_id] = normalized_base
    await Storage.set(
        f"{_CALLABLE_PREFIX}{session_id}",
        {
            "tools": sorted(normalized),
            "agent": agent_name,
            "base_tools": sorted(normalized_base),
        },
        "session_callable_tools",
    )
    return set(normalized)


async def add_session_callable_tools(
    session_id: str,
    tool_names: Iterable[str],
    *,
    agent_name: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Set[str]:
    current = await get_session_callable_tools(session_id)
    requested = _normalize_tool_names(tool_names)
    if agent_name:
        try:
            from flocks.agent.controls import agent_allows_tool, rex_session_uses_full_tool_catalog

            if not await rex_session_uses_full_tool_catalog(session_id, agent_name, extra):
                allowed = {
                    name
                    for name in requested
                    if await agent_allows_tool(agent_name, name)
                }
                requested = allowed
        except Exception:
            requested = set()
    current.update(requested)
    return await set_session_callable_tools(
        session_id,
        current,
        agent_name=agent_name or await get_session_callable_agent(session_id),
        base_tool_names=await get_session_callable_base_tools(session_id),
    )


async def initialize_session_callable_tools(
    session_id: str,
    base_tool_names: Iterable[str],
    *,
    always_load_tool_names: Optional[Iterable[str]] = None,
    agent_name: Optional[str] = None,
) -> Set[str]:
    combined = set(_normalize_tool_names(base_tool_names))
    combined.update(_normalize_tool_names(always_load_tool_names or []))
    return await set_session_callable_tools(
        session_id,
        combined,
        agent_name=agent_name,
        base_tool_names=base_tool_names,
    )


async def clear_session_callable_tools(session_id: str) -> None:
    _cache.pop(session_id, None)
    _agent_cache.pop(session_id, None)
    _base_cache.pop(session_id, None)
    await Storage.delete(f"{_CALLABLE_PREFIX}{session_id}")


async def session_can_call_tool(session_id: str, tool_name: str) -> bool:
    return tool_name in await get_session_callable_tools(session_id)
