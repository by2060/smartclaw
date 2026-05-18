"""Runtime enforcement helpers for agent-scoped capabilities."""

from __future__ import annotations

from typing import Any, Iterable, Optional


def _normalize_names(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


async def _canonical_agent_name(name: Optional[str]) -> str:
    if not name:
        return ""
    from flocks.agent.registry import AGENT_ALIASES, Agent

    resolved = AGENT_ALIASES.get(name, name)
    agent = await Agent.get(resolved)
    return (agent.name if agent else resolved).strip().lower()


async def agent_allows_subagent(parent_agent_name: Optional[str], child_agent_name: Optional[str]) -> bool:
    """Return whether parent agent may delegate to child.

    Missing ``sub_agents`` means unrestricted for backward compatibility. An
    explicit empty list means delegation is disabled.
    """
    if not child_agent_name:
        return False

    from flocks.agent.registry import Agent

    parent = await Agent.get(parent_agent_name or "")
    if not parent:
        return True

    allowed = getattr(parent, "sub_agents", None)
    if allowed is None:
        return True
    if len(allowed) == 0:
        return False

    allowed_names = _normalize_names(allowed)
    for allowed_name in allowed:
        canonical_allowed = await _canonical_agent_name(str(allowed_name))
        if canonical_allowed:
            allowed_names.add(canonical_allowed)
    child = await _canonical_agent_name(child_agent_name)
    return child in allowed_names or str(child_agent_name).strip().lower() in allowed_names


async def agent_allowed_subagents(parent_agent_name: Optional[str]) -> list[str]:
    from flocks.agent.registry import Agent

    parent = await Agent.get(parent_agent_name or "")
    if not parent:
        return []
    return list(getattr(parent, "sub_agents", None) or [])


async def agent_allows_tool(agent_name: Optional[str], tool_name: Optional[str]) -> bool:
    """Return whether agent may execute a tool.

    Tool permission is:
    - always-load tools are always allowed;
    - tools explicitly declared in ``agent.yaml`` are allowed;
    - declaring ``tool_search`` grants dynamic access to the full tool catalog.

    Unknown agents are allowed for backward compatibility with tests and
    non-session utility contexts that do not bind a real agent.
    """
    if not tool_name:
        return False
    if not agent_name:
        return True

    from flocks.agent.registry import Agent
    from flocks.tool.catalog import get_always_load_tool_names

    normalized_tool = str(tool_name).strip()
    if normalized_tool in get_always_load_tool_names():
        return True

    agent = await Agent.get(agent_name or "")
    if not agent:
        return True

    declared = {
        str(value).strip()
        for value in (getattr(agent, "tools", None) or [])
        if str(value).strip()
    }
    if "tool_search" in declared:
        return True
    return normalized_tool in declared


async def agent_allowed_tools(agent_name: Optional[str]) -> list[str]:
    from flocks.agent.registry import Agent

    if not agent_name:
        return []
    agent = await Agent.get(agent_name or "")
    if not agent:
        return []
    return list(getattr(agent, "tools", None) or [])


async def agent_allows_skill(agent_name: Optional[str], skill_name: Optional[str]) -> bool:
    """Return whether agent may load the requested skill.

    Missing ``skills`` means unrestricted for backward compatibility. An
    explicit empty list means no skills are allowed.
    """
    if not skill_name:
        return False
    if not agent_name:
        return True

    from flocks.agent.registry import Agent

    agent = await Agent.get(agent_name or "")
    if not agent:
        return True

    allowed = getattr(agent, "skills", None)
    if allowed is None:
        return True

    return str(skill_name).strip().lower() in _normalize_names(allowed)


async def agent_allowed_skills(agent_name: Optional[str]) -> list[str]:
    from flocks.agent.registry import Agent

    if not agent_name:
        return []
    agent = await Agent.get(agent_name or "")
    if not agent:
        return []
    return list(getattr(agent, "skills", None) or [])


async def agent_skill_allowlist(agent_name: Optional[str]) -> Optional[list[str]]:
    """Return the explicit skill allowlist, or None when unrestricted."""
    from flocks.agent.registry import Agent

    if not agent_name:
        return None
    agent = await Agent.get(agent_name or "")
    if not agent:
        return None
    allowed = getattr(agent, "skills", None)
    return list(allowed) if allowed is not None else None


def filter_agent_skills(agent: Any, skills: Iterable[Any]) -> list[Any]:
    """Filter discovered skills according to an AgentInfo.skills declaration.

    ``skills is None`` on the agent means unrestricted. ``skills == []`` means
    deny all.
    """
    allowed = getattr(agent, "skills", None)
    skill_list = list(skills)
    if allowed is None:
        return skill_list

    allowed_names = _normalize_names(allowed)
    return [
        skill
        for skill in skill_list
        if str(getattr(skill, "name", "")).strip().lower() in allowed_names
    ]


def filter_agent_tools(agent: Any, tools: Iterable[Any]) -> list[Any]:
    """Filter prompt-visible tools according to ``agent.yaml`` tools.

    Always-load tools are visible for every agent. Declaring ``tool_search``
    keeps the full catalog visible because dynamic tool discovery is enabled.
    """
    tool_list = list(tools)
    declared = {
        str(value).strip()
        for value in (getattr(agent, "tools", None) or [])
        if str(value).strip()
    }
    if "tool_search" in declared:
        return tool_list

    try:
        from flocks.tool.catalog import get_always_load_tool_names

        always_load = get_always_load_tool_names()
    except Exception:
        always_load = set()

    allowed = declared | set(always_load)
    return [
        tool
        for tool in tool_list
        if str(getattr(tool, "name", "")).strip() in allowed
    ]


def filter_agent_subagents(agent: Any, available_agents: Iterable[Any]) -> list[Any]:
    """Filter delegation prompt candidates according to AgentInfo.sub_agents."""
    allowed = getattr(agent, "sub_agents", None)
    candidates = list(available_agents)
    if allowed is None:
        return candidates
    if len(allowed) == 0:
        return []

    allowed_names = _normalize_names(allowed)
    return [
        candidate
        for candidate in candidates
        if str(getattr(candidate, "name", "")).strip().lower() in allowed_names
    ]
