"""Runtime enforcement helpers for agent-scoped capabilities."""

from __future__ import annotations

from typing import Any, Iterable, Optional


_TITAN_AGENT_NAMES = {"titan", "sisyphus"}


def _normalize_names(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def is_titan_agent(agent_name: Optional[str]) -> bool:
    return str(agent_name or "").strip().lower() in _TITAN_AGENT_NAMES


async def _canonical_agent_name(name: Optional[str]) -> str:
    if not name:
        return ""
    from smartclaw.agent.registry import AGENT_ALIASES, Agent

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

    from smartclaw.agent.registry import Agent

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
    from smartclaw.agent.registry import Agent

    parent = await Agent.get(parent_agent_name or "")
    if not parent:
        return []
    return list(getattr(parent, "sub_agents", None) or [])


def _session_user_context(session: Any) -> dict[str, Any]:
    context = getattr(session, "user_context", None)
    return dict(context) if isinstance(context, dict) else {}


async def titan_session_allowed_subagents(session_id: Optional[str]) -> Optional[list[str]]:
    """Return Titan's dynamic subagent allowlist for a session.

    ``None`` means no dynamic restriction was supplied. An explicit empty list
    means Titan may not delegate to any subagent.
    """
    if not session_id:
        return None

    try:
        from smartclaw.session.session import Session

        session = await Session.get_by_id(session_id)
    except Exception:
        session = None
    if not session:
        return None

    context = _session_user_context(session)
    raw_allowed = context.get("allowedSubagents", None)
    if raw_allowed is None:
        return None
    if isinstance(raw_allowed, str):
        raw_allowed = [raw_allowed]
    if not isinstance(raw_allowed, Iterable):
        return []
    return [
        str(value).strip()
        for value in raw_allowed
        if str(value).strip()
    ]


async def titan_session_allows_subagent(
    session_id: Optional[str],
    parent_agent_name: Optional[str],
    child_agent_name: Optional[str],
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """Return whether Titan may delegate to ``child_agent_name`` in this session.

    Dynamic userContext scoping applies only to Titan. Other agents are governed
    solely by their own ``agent.yaml`` controls.
    """
    if not await agent_allows_subagent(parent_agent_name, child_agent_name):
        return False
    if not is_titan_agent(parent_agent_name):
        return True

    workflow_context = (
        isinstance(extra, dict)
        and bool(extra.get("workflow_tool_context"))
    ) or await _session_is_workflow(session_id)
    if workflow_context and not await _agent_is_l1(child_agent_name):
        return False

    allowed = await titan_session_allowed_subagents(session_id)
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
    return child in allowed_names or str(child_agent_name or "").strip().lower() in allowed_names


async def titan_session_allowed_subagents_text(
    session_id: Optional[str],
    parent_agent_name: Optional[str],
) -> str:
    if is_titan_agent(parent_agent_name):
        dynamic_allowed = await titan_session_allowed_subagents(session_id)
        if dynamic_allowed is not None:
            return ", ".join(dynamic_allowed) or "none"
    allowed = await agent_allowed_subagents(parent_agent_name)
    return ", ".join(allowed) or "none"


async def titan_session_uses_full_tool_catalog(
    session_id: Optional[str],
    agent_name: Optional[str],
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """Return whether Titan should see/execute the full tool catalog.

    This is intentionally Titan-only. Other agents remain scoped to their own
    ``agent.yaml.tools`` even when they run inside a workflow-related session.
    """
    if not is_titan_agent(agent_name):
        return False

    if isinstance(extra, dict) and extra.get("workflow_tool_context"):
        return True

    return await _session_is_workflow(session_id)


async def titan_session_uses_full_skill_catalog(
    session_id: Optional[str],
    agent_name: Optional[str],
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """Return whether Titan should be allowed to load/manage all skills."""
    if not is_titan_agent(agent_name):
        return False

    if isinstance(extra, dict) and extra.get("workflow_tool_context"):
        return True

    return await _session_is_workflow(session_id)


async def _session_is_workflow(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    try:
        from smartclaw.session.session import Session

        session = await Session.get_by_id(session_id)
    except Exception:
        session = None
    return str(getattr(session, "category", "") or "").strip().lower() == "workflow"


async def _agent_is_l1(agent_name: Optional[str]) -> bool:
    if not agent_name:
        return False

    from smartclaw.agent.registry import Agent

    agent = await Agent.get(agent_name or "")
    if not agent:
        return False

    if str(getattr(agent, "mode", "") or "").strip().lower() == "l1":
        return True
    if str(getattr(agent, "agent_type", "") or "").strip().lower() == "l1":
        return True

    options = getattr(agent, "options", None)
    if isinstance(options, dict):
        for key in ("agent_type", "agentType", "level"):
            if str(options.get(key) or "").strip().lower() == "l1":
                return True
    return False


async def agent_allows_tool(agent_name: Optional[str], tool_name: Optional[str]) -> bool:
    """Return whether agent may execute a tool.

    Tool permission is:
    - always-load tools are always allowed;
    - tools explicitly declared in ``agent.yaml`` are allowed;

    Unknown agents are allowed for backward compatibility with tests and
    non-session utility contexts that do not bind a real agent.
    """
    if not tool_name:
        return False
    if not agent_name:
        return True

    from smartclaw.agent.registry import Agent
    from smartclaw.tool.catalog import get_always_load_tool_names

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
    return normalized_tool in declared


async def agent_allowed_tools(agent_name: Optional[str]) -> list[str]:
    from smartclaw.agent.registry import Agent

    if not agent_name:
        return []
    agent = await Agent.get(agent_name or "")
    if not agent:
        return []
    return list(getattr(agent, "tools", None) or [])


_WORKFLOW_LIST_TOKENS = {
    "list",
    "view",
    "read",
    "workflow:list",
    "workflow:view",
    "workflow:read",
    "workflows:list",
    "workflows:view",
}

_WORKFLOW_RUN_TOKENS = {
    "run",
    "execute",
    "workflow:run",
    "workflow:execute",
    "workflows:run",
    "workflows:execute",
}

_WORKFLOW_ALL_TOKENS = {"*", "all"}


async def titan_session_uses_full_workflow_catalog(
    session_id: Optional[str],
    agent_name: Optional[str],
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """Return whether Titan may view the full workflow catalog."""
    if not is_titan_agent(agent_name):
        return False

    if isinstance(extra, dict) and extra.get("workflow_tool_context"):
        return True

    return await _session_is_workflow(session_id)


async def agent_visible_workflow_ids(
    agent_name: Optional[str],
    *,
    session_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[set[str]]:
    """Return visible workflow IDs, or None when the full catalog is visible."""
    effective_agent = str(agent_name or "titan").strip() or "titan"
    if await titan_session_uses_full_workflow_catalog(session_id, effective_agent, extra):
        return None

    from smartclaw.agent.registry import Agent

    agent = await Agent.get(effective_agent)
    if not agent:
        return set()

    declared_raw = getattr(agent, "workflows", None)
    if not declared_raw:
        return set()

    declared = _normalize_names(declared_raw)
    if declared & _WORKFLOW_ALL_TOKENS:
        return None

    hidden_tokens = _WORKFLOW_LIST_TOKENS | _WORKFLOW_RUN_TOKENS
    return {
        item
        for item in declared
        if item and item not in hidden_tokens and not item.startswith("workflow:") and not item.startswith("workflows:")
    }


async def agent_allows_workflow_listing(
    agent_name: Optional[str],
    *,
    session_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """Return whether an agent may enumerate workflow names/descriptions."""
    visible_ids = await agent_visible_workflow_ids(agent_name, session_id=session_id, extra=extra)
    return visible_ids is None or bool(visible_ids)


async def filter_workflow_entries_for_agent(
    entries: Iterable[Any],
    agent_name: Optional[str],
    *,
    session_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> list[Any]:
    """Filter workflow catalog entries for an agent-scoped conversation view."""
    entry_list = list(entries)
    visible_ids = await agent_visible_workflow_ids(agent_name, session_id=session_id, extra=extra)
    if visible_ids is None:
        return entry_list
    if not visible_ids:
        return []

    def entry_id(entry: Any) -> str:
        if isinstance(entry, dict):
            return str(entry.get("id") or entry.get("workflowId") or entry.get("name") or "").strip().lower()
        return str(
            getattr(entry, "id", None)
            or getattr(entry, "workflowId", None)
            or getattr(entry, "name", None)
            or ""
        ).strip().lower()

    return [entry for entry in entry_list if entry_id(entry) in visible_ids]


async def titan_session_uses_full_workflow_execution(
    session_id: Optional[str],
    agent_name: Optional[str],
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """Return whether Titan may execute workflows without agent.yaml workflow grants."""
    if not is_titan_agent(agent_name):
        return False

    if isinstance(extra, dict) and extra.get("workflow_tool_context"):
        return True

    return await _session_is_workflow(session_id)


def _workflow_id_from_canonical_path(value: Optional[Any]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        from pathlib import Path

        parts = Path(raw).expanduser().parts
    except Exception:
        return None

    for index in range(0, max(len(parts) - 3, 0)):
        if parts[index:index + 3] != (".smartclaw", "plugins", "workflows"):
            continue
        workflow_index = index + 3
        if workflow_index >= len(parts):
            continue
        workflow_id = str(parts[workflow_index]).strip()
        if not workflow_id:
            continue
        remainder = parts[workflow_index + 1:]
        if len(remainder) == 1 and str(remainder[0]).lower() == "workflow.json":
            return workflow_id.lower()
    return None


def _workflow_candidate_tokens(
    *,
    workflow_id: Optional[str] = None,
    workflow_path: Optional[str] = None,
) -> set[str]:
    tokens: set[str] = set()

    def add_id(value: Optional[Any]) -> None:
        text = str(value or "").strip()
        if not text:
            return
        canonical_from_path = _workflow_id_from_canonical_path(text)
        if canonical_from_path:
            tokens.add(canonical_from_path)
            return
        if "/" in text or "\\" in text or text.endswith(".json"):
            return
        tokens.add(text.lower())

    add_id(workflow_id)
    canonical_path_id = _workflow_id_from_canonical_path(workflow_path)
    if canonical_path_id:
        tokens.add(canonical_path_id)
    return tokens


async def agent_allowed_workflows(agent_name: Optional[str]) -> list[str]:
    from smartclaw.agent.registry import Agent

    if not agent_name:
        return []
    agent = await Agent.get(agent_name or "")
    if not agent:
        return []
    return list(getattr(agent, "workflows", None) or [])


async def agent_allows_workflow_execution(
    agent_name: Optional[str],
    *,
    workflow_id: Optional[str] = None,
    workflow_path: Optional[str] = None,
    session_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> bool:
    """Return whether an agent may execute the requested workflow.

    Missing ``workflows`` and an explicit empty list both deny execution. Titan
    workflow sessions are exempt so existing workflow-category sessions keep
    their current behavior.
    """
    if await titan_session_uses_full_workflow_execution(session_id, agent_name, extra):
        return True
    if not (workflow_id or workflow_path):
        return False
    if not agent_name:
        return False

    from smartclaw.agent.registry import Agent

    agent = await Agent.get(agent_name or "")
    if not agent:
        return False

    declared_raw = getattr(agent, "workflows", None)
    if not declared_raw:
        return False

    declared = _normalize_names(declared_raw)
    if declared & (_WORKFLOW_ALL_TOKENS | _WORKFLOW_RUN_TOKENS):
        return True

    candidates = _workflow_candidate_tokens(
        workflow_id=workflow_id,
        workflow_path=workflow_path,
    )
    return bool(declared & candidates)


async def agent_allows_skill(agent_name: Optional[str], skill_name: Optional[str]) -> bool:
    """Return whether agent may load the requested skill.

    Missing ``skills`` means unrestricted for backward compatibility. An
    explicit empty list means no skills are allowed.
    """
    if not skill_name:
        return False
    if not agent_name:
        return True

    from smartclaw.agent.registry import Agent

    agent = await Agent.get(agent_name or "")
    if not agent:
        return True

    allowed = getattr(agent, "skills", None)
    if allowed is None:
        return True

    return str(skill_name).strip().lower() in _normalize_names(allowed)


async def agent_allowed_skills(agent_name: Optional[str]) -> list[str]:
    from smartclaw.agent.registry import Agent

    if not agent_name:
        return []
    agent = await Agent.get(agent_name or "")
    if not agent:
        return []
    return list(getattr(agent, "skills", None) or [])


async def agent_skill_allowlist(agent_name: Optional[str]) -> Optional[list[str]]:
    """Return the explicit skill allowlist, or None when unrestricted."""
    from smartclaw.agent.registry import Agent

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
    only exposes the search tool itself; it does not expand visibility to the
    full catalog.
    """
    tool_list = list(tools)
    declared = {
        str(value).strip()
        for value in (getattr(agent, "tools", None) or [])
        if str(value).strip()
    }
    try:
        from smartclaw.tool.catalog import get_always_load_tool_names

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
