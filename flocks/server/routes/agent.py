"""
Agent management routes

Routes for listing and managing agents.

Flocks TUI expects Agent format:
{
    "name": string,
    "description"?: string,
    "descriptionCn"?: string,
    "mode": "subagent" | "primary" | "all",
    "native"?: boolean,
    "hidden"?: boolean,
    "topP"?: number,
    "temperature"?: number,
    "color"?: string,
    "permission": PermissionRuleset,  // Required
    "model"?: { modelID, providerID },
    "prompt"?: string,
    "options": { [key]: unknown },    // Required
}
"""

import asyncio
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flocks.agent.registry import Agent
from flocks.agent.agent import AgentInfo as AgentInfoModel, AgentModel as AgentModelConfig
from flocks.agent.agent_factory import (
    create_yaml_agent,
    find_yaml_agent,
    load_agent,
    read_yaml_agent,
    update_yaml_agent,
    delete_yaml_agent,
)
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.agent")

# Lazily-initialised lock to prevent concurrent read-modify-write on model_overrides.
# Created on first use so it is always bound to the running event loop.
_model_overrides_lock: Optional[asyncio.Lock] = None


def _get_overrides_lock() -> asyncio.Lock:
    global _model_overrides_lock
    if _model_overrides_lock is None:
        _model_overrides_lock = asyncio.Lock()
    return _model_overrides_lock


def _dedupe_strings(values: Optional[List[str]]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _agent_overlay(
    *,
    delegatable: Optional[bool] = None,
    skills: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    sub_agents: Optional[List[str]] = None,
    kb: Optional[List[str]] = None,
) -> Dict[str, Any]:
    overlay: Dict[str, Any] = {}
    if delegatable is not None:
        overlay["delegatable"] = delegatable
    if skills is not None:
        overlay["skills"] = _dedupe_strings(skills)
    if tools is not None:
        overlay["tools"] = _dedupe_strings(tools)
    if sub_agents is not None:
        overlay["sub_agents"] = _dedupe_strings(sub_agents)
    if kb is not None:
        overlay["kb"] = _dedupe_strings(kb)
    return overlay


class AgentModelInfo(BaseModel):
    """Agent model configuration"""
    modelID: str
    providerID: str


class AgentResponse(BaseModel):
    """
    Agent response - Flocks TUI compatible format.

    Includes required 'permission' and 'options' fields.
    """
    name: str
    description: Optional[str] = None
    descriptionCn: Optional[str] = None
    mode: str = "primary"
    native: Optional[bool] = True
    hidden: Optional[bool] = False
    topP: Optional[float] = None
    temperature: Optional[float] = None
    color: Optional[str] = None
    permission: List[Dict[str, Any]] = Field(default_factory=list)
    model: Optional[AgentModelInfo] = None
    prompt: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)
    delegatable: bool = True
    steps: Optional[int] = None
    skills: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    sub_agents: List[str] = Field(default_factory=list)
    kb: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


# =============================================================================
# Internal helpers
# =============================================================================

def agent_to_response(
    agent: AgentInfoModel,
    model_override: Optional[Dict[str, str]] = None,
    temperature_override: Optional[float] = None,
    skills: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    sub_agents: Optional[List[str]] = None,
    kb: Optional[List[str]] = None,
    delegatable_override: Optional[bool] = None,
) -> AgentResponse:
    """Convert internal AgentInfo to API response format."""
    if delegatable_override is not None:
        delegatable = delegatable_override
    else:
        delegatable = agent.delegatable if agent.delegatable is not None else True

    if model_override:
        model_info = AgentModelInfo(
            modelID=model_override["modelID"],
            providerID=model_override["providerID"],
        )
    elif agent.model:
        model_info = AgentModelInfo(
            modelID=agent.model.model_id,
            providerID=agent.model.provider_id,
        )
    else:
        model_info = None

    return AgentResponse(
        name=agent.name,
        description=agent.description,
        descriptionCn=agent.description_cn,
        mode=agent.mode,
        native=agent.native,
        hidden=agent.hidden,
        topP=agent.top_p,
        temperature=temperature_override if temperature_override is not None else agent.temperature,
        color=agent.color,
        permission=[],
        model=model_info,
        prompt=agent.prompt,
        options=agent.options,
        delegatable=delegatable,
        steps=agent.steps,
        skills=skills if skills is not None else (getattr(agent, "skills", None) or []),
        tools=tools if tools is not None else (agent.tools or []),
        sub_agents=sub_agents if sub_agents is not None else (getattr(agent, "sub_agents", None) or []),
        kb=kb if kb is not None else (getattr(agent, "kb", None) or []),
        tags=agent.tags,
    )


def _agent_data_to_info(agent_data: Dict[str, Any]) -> AgentInfoModel:
    """Build an in-memory AgentInfo from a custom agent's stored data dict.

    Used after create/update to keep ``_custom_agents`` in sync with Storage.
    """
    model_data = agent_data.get("model")
    return AgentInfoModel(
        name=agent_data["name"],
        description=agent_data.get("description") or "",
        description_cn=agent_data.get("description_cn") or agent_data.get("descriptionCn"),
        prompt=agent_data.get("prompt") or "",
        temperature=agent_data.get("temperature"),
        color=agent_data.get("color"),
        mode=agent_data.get("mode", "primary"),
        model=AgentModelConfig(
            model_id=model_data["modelID"],
            provider_id=model_data["providerID"],
        ) if model_data else None,
        native=False,
        hidden=False,
        tools=agent_data.get("tools", []),
        skills=agent_data.get("skills") if "skills" in agent_data else None,
        sub_agents=agent_data.get("sub_agents") if "sub_agents" in agent_data else None,
        kb=agent_data.get("kb") if "kb" in agent_data else agent_data.get("knowledge_base"),
        delegatable=agent_data.get("delegatable"),
    )


def _custom_agent_data_to_response(agent_data: Dict[str, Any]) -> AgentResponse:
    """Build an AgentResponse from a custom agent's stored data dict."""
    model_data = agent_data.get("model")
    model_info = AgentModelInfo(**model_data) if model_data else None
    return AgentResponse(
        name=agent_data["name"],
        description=agent_data.get("description"),
        descriptionCn=agent_data.get("description_cn") or agent_data.get("descriptionCn"),
        prompt=agent_data.get("prompt"),
        temperature=agent_data.get("temperature"),
        color=agent_data.get("color"),
        mode=agent_data.get("mode", "primary"),
        model=model_info,
        native=agent_data.get("native", False),
        hidden=agent_data.get("hidden", False),
        permission=[],
        options={},
        skills=agent_data.get("skills", []),
        tools=agent_data.get("tools", []),
        sub_agents=agent_data.get("sub_agents", []),
        kb=agent_data.get("kb", agent_data.get("knowledge_base", [])),
        tags=agent_data.get("tags", []),
        delegatable=agent_data.get("delegatable", True),
    )


async def _load_model_overrides() -> Dict[str, Dict[str, Any]]:
    """Load all agent overrides from storage. Returns {agent_name: {modelID?, providerID?, temperature?}}"""
    from flocks.storage.storage import Storage
    try:
        data = await Storage.read("agent/model_overrides")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _load_agent_overlay(name: str) -> Dict[str, Any]:
    """Load the agent Storage overlay, if present.

    Works for both full Storage-based custom agents and YAML agents with an
    overlay. For YAML agents, the overlay mirrors runtime-control fields from
    agent.yaml so API reads and cached runtime state stay in sync.
    """
    from flocks.storage.storage import Storage
    try:
        data = await Storage.read(f"agent/custom/{name}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_all_tool_names() -> List[str]:
    """Return all registered tool names, initialising ToolRegistry if needed."""
    from flocks.tool.registry import ToolRegistry
    ToolRegistry.init()
    return [t.name for t in ToolRegistry.list_tools()]


def _compute_native_agent_tools(agent: AgentInfoModel, all_tool_names: List[str]) -> List[str]:
    """Return the concrete tools explicitly declared for the agent."""
    tools = list(agent.tools or [])
    if tools:
        return [name for name in tools if name in all_tool_names]
    return []


async def _build_single_agent_response(
    agent: AgentInfoModel,
    overrides: Dict[str, Dict[str, Any]],
    all_tool_names: List[str],
) -> AgentResponse:
    """Build AgentResponse for one agent, resolving model overrides and tools/skills."""
    overlay = await _load_agent_overlay(agent.name)
    if agent.native:
        tools = overlay.get("tools") if "tools" in overlay else _compute_native_agent_tools(agent, all_tool_names)
        skills = overlay.get("skills") if "skills" in overlay else getattr(agent, "skills", [])
    else:
        skills = overlay.get("skills", getattr(agent, "skills", []))
        tools = overlay.get("tools", agent.tools or [])
    sub_agents = overlay.get("sub_agents") if "sub_agents" in overlay else getattr(agent, "sub_agents", [])
    kb = (
        overlay.get("kb") if "kb" in overlay
        else overlay.get("knowledge_base") if "knowledge_base" in overlay
        else getattr(agent, "kb", [])
    )
    delegatable = overlay.get("delegatable") if "delegatable" in overlay else None
    override = overrides.get(agent.name, {})
    model_override = {k: override[k] for k in ("modelID", "providerID") if k in override} or None
    temperature_override = override.get("temperature")
    return agent_to_response(
        agent,
        model_override=model_override,
        temperature_override=temperature_override,
        skills=skills,
        tools=tools,
        sub_agents=sub_agents,
        kb=kb,
        delegatable_override=delegatable,
    )


# =============================================================================
# Refresh
# =============================================================================

@router.post("/refresh", summary="Reload agents from all sources")
async def refresh_agents():
    """Invalidate agent cache and reload from disk (plugins, YAML, etc.)."""
    try:
        agents = await Agent.refresh()
        return {"count": len(agents)}
    except Exception as e:
        log.error("agent.refresh.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Read routes
# =============================================================================

@router.get("", response_model=List[AgentResponse], summary="List agents")
async def list_agents():
    """List all available agents with model overrides applied."""
    try:
        agents = await Agent.list()
        overrides = await _load_model_overrides()
        all_tool_names = _get_all_tool_names()
        result = []
        for agent in agents:
            if agent.hidden:
                continue
            result.append(await _build_single_agent_response(agent, overrides, all_tool_names))
        return result
    except Exception as e:
        log.error("agent.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{name}", response_model=AgentResponse, summary="Get agent")
async def get_agent(name: str):
    """Get a specific agent by name with model override applied."""
    try:
        agent = await Agent.get(name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {name} not found")
        overrides = await _load_model_overrides()
        all_tool_names = _get_all_tool_names()
        return await _build_single_agent_response(agent, overrides, all_tool_names)
    except HTTPException:
        raise
    except Exception as e:
        log.error("agent.get.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{name}/prompt", response_model=Dict[str, str], summary="Get agent prompt")
async def get_agent_prompt(name: str):
    """Get the system prompt for an agent."""
    try:
        agent = await Agent.get(name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {name} not found")
        return {"prompt": agent.prompt or ""}
    except HTTPException:
        raise
    except Exception as e:
        log.error("agent.prompt.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Custom Agent CRUD
# =============================================================================

class AgentCreateRequest(BaseModel):
    """Request to create a custom agent"""
    model_config = {"populate_by_name": True}

    name: str = Field(..., description="Agent name")
    description: Optional[str] = Field(None, description="Agent description (English; used for delegation)")
    descriptionCn: Optional[str] = Field(None, description="Chinese UI description")
    prompt: str = Field(..., description="System prompt")
    temperature: Optional[float] = Field(None, description="Temperature")
    color: Optional[str] = Field(None, description="Color")
    mode: str = Field("primary", description="Agent mode")
    model: Optional[AgentModelInfo] = Field(None, description="Preferred model")
    delegatable: bool = Field(False, description="Whether this agent can be delegated to")
    skills: Optional[List[str]] = Field(None, description="Enabled skill names")
    tools: List[str] = Field(default_factory=list, description="Enabled tool names")
    sub_agents: Optional[List[str]] = Field(None, description="Allowed L1 execution agent names")
    kb: Optional[List[str]] = Field(None, alias="knowledge_base", description="Allowed knowledge base dataset IDs")


class AgentUpdateRequest(BaseModel):
    """Request to update a custom agent"""
    model_config = {"populate_by_name": True}
    description: Optional[str] = Field(None, description="Agent description (English; used for delegation)")
    descriptionCn: Optional[str] = Field(None, description="Chinese UI description")
    prompt: Optional[str] = Field(None, description="System prompt")
    temperature: Optional[float] = Field(None, description="Temperature")
    color: Optional[str] = Field(None, description="Color")
    model: Optional[AgentModelInfo] = Field(None, description="Preferred model")
    delegatable: Optional[bool] = Field(None, description="Whether this agent can be delegated to")
    skills: Optional[List[str]] = Field(None, description="Enabled skill names")
    tools: Optional[List[str]] = Field(None, description="Enabled tool names")
    sub_agents: Optional[List[str]] = Field(None, description="Allowed L1 execution agent names")
    kb: Optional[List[str]] = Field(None, alias="knowledge_base", description="Allowed knowledge base dataset IDs")


class AgentModelUpdateRequest(BaseModel):
    """Request to update the model (and optionally temperature) for any agent (native or custom)"""
    model: Optional[AgentModelInfo] = Field(None, description="New model, or null to reset to default")
    temperature: Optional[float] = Field(None, description="Temperature override for native agents")


@router.post("", response_model=AgentResponse, summary="Create custom agent")
async def create_agent(req: AgentCreateRequest):
    """
    Create a custom agent

    Saves custom agent configuration as a project-level YAML plugin agent.
    """
    try:
        existing = await Agent.get(req.name)
        if existing:
            raise HTTPException(status_code=409, detail=f"Agent {req.name} already exists")

        overlay = _agent_overlay(
            delegatable=req.delegatable,
            skills=req.skills,
            tools=req.tools,
            sub_agents=req.sub_agents,
            kb=req.kb,
        )
        agent_data: Dict[str, Any] = {
            "name": req.name,
            "description": req.description,
            "description_cn": req.descriptionCn,
            "temperature": req.temperature,
            "color": req.color,
            "mode": req.mode,
            "model": (
                {"provider_id": req.model.providerID, "model_id": req.model.modelID}
                if req.model
                else None
            ),
            "hidden": False,
            **overlay,
        }
        yaml_path = create_yaml_agent(agent_data, prompt=req.prompt)
        from flocks.storage.storage import Storage
        await Storage.write(f"agent/custom/{req.name}", overlay)
        from flocks.agent.registry import Agent as AgentRegistry

        loaded_agent = load_agent(yaml_path.parent, native=True)
        if loaded_agent is None:
            loaded_agent = _agent_data_to_info({
                **agent_data,
                "prompt": req.prompt,
                "native": True,
                "model": req.model.model_dump() if req.model else None,
            })
        AgentRegistry.register(req.name, loaded_agent)
        AgentRegistry.invalidate_cache()
        log.info("agent.created", {"name": req.name, "path": str(yaml_path)})
        return agent_to_response(
            loaded_agent,
            skills=overlay.get("skills"),
            tools=overlay.get("tools"),
            sub_agents=overlay.get("sub_agents"),
            kb=overlay.get("kb"),
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("agent.create.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{name}", response_model=AgentResponse, summary="Update custom agent")
async def update_agent(name: str, req: AgentUpdateRequest):
    """
    Update a custom agent (Storage-based or YAML plugin).
    """
    from flocks.storage.storage import Storage

    try:
        # --- Try Storage-based custom agent first ---
        agent_key = f"agent/custom/{name}"
        agent_data: Optional[Dict[str, Any]] = None
        try:
            agent_data = await Storage.read(agent_key)
        except Storage.NotFoundError:
            pass

        # Storage-based custom agents always have a "name" key.
        # YAML agents may also have an overlay entry (skills/tools only) which
        # lacks the "name" key; those should fall through to the YAML path.
        if agent_data is not None and agent_data.get("name"):
            if req.description is not None:
                agent_data["description"] = req.description
            if req.descriptionCn is not None:
                agent_data["description_cn"] = req.descriptionCn
            if req.prompt is not None:
                agent_data["prompt"] = req.prompt
            if req.temperature is not None:
                agent_data["temperature"] = req.temperature
            if req.color is not None:
                agent_data["color"] = req.color
            if req.model is not None:
                agent_data["model"] = req.model.model_dump()
            overlay = _agent_overlay(
                delegatable=req.delegatable if req.delegatable is not None else agent_data.get("delegatable"),
                skills=(
                    req.skills if req.skills is not None
                    else agent_data.get("skills") if "skills" in agent_data else None
                ),
                tools=req.tools if req.tools is not None else agent_data.get("tools", []),
                sub_agents=(
                    req.sub_agents if req.sub_agents is not None
                    else agent_data.get("sub_agents") if "sub_agents" in agent_data else None
                ),
                kb=(
                    req.kb if req.kb is not None
                    else agent_data.get("kb") if "kb" in agent_data else agent_data.get("knowledge_base")
                ),
            )
            agent_data.update(overlay)

            await Storage.write(agent_key, agent_data)

            from flocks.agent.registry import Agent as AgentRegistry
            AgentRegistry.register(name, _agent_data_to_info(agent_data))
            AgentRegistry.invalidate_cache()

            log.info("agent.updated", {"name": name, "source": "storage"})
            return _custom_agent_data_to_response(agent_data)

        # --- Fall back to YAML plugin agent ---
        if find_yaml_agent(name) is not None:
            yaml_data = read_yaml_agent(name) or {}
            updates: Dict[str, Any] = {}
            if req.description is not None:
                updates["description"] = req.description
            if req.descriptionCn is not None:
                updates["description_cn"] = req.descriptionCn
            if req.prompt is not None:
                updates["prompt"] = req.prompt
            if req.temperature is not None:
                updates["temperature"] = req.temperature
            if req.color is not None:
                updates["color"] = req.color
            if req.model is not None:
                updates["model"] = req.model.model_dump()
            updates.update(_agent_overlay(
                delegatable=req.delegatable if req.delegatable is not None else yaml_data.get("delegatable"),
                skills=(
                    req.skills if req.skills is not None
                    else yaml_data.get("skills") if "skills" in yaml_data else None
                ),
                tools=req.tools if req.tools is not None else yaml_data.get("tools", []),
                sub_agents=(
                    req.sub_agents if req.sub_agents is not None
                    else yaml_data.get("sub_agents") if "sub_agents" in yaml_data else None
                ),
                kb=(
                    req.kb if req.kb is not None
                    else yaml_data.get("kb") if "kb" in yaml_data else yaml_data.get("knowledge_base")
                ),
            ))

            if not update_yaml_agent(name, updates):
                raise HTTPException(status_code=500, detail=f"Failed to write YAML for agent {name}")

            # Persist the runtime-control overlay for YAML agents in Storage.
            # The entry intentionally omits "name" so it is not mistaken for a
            # full Storage-based custom agent on subsequent updates.
            extras: Dict[str, Any] = agent_data if isinstance(agent_data, dict) else {}
            for key in ("delegatable", "skills", "tools", "sub_agents", "kb"):
                if key in updates:
                    extras[key] = updates[key]
            await Storage.write(agent_key, extras)

            # Sync: apply updates to the in-memory AgentInfo cache
            agent = await Agent.get(name)
            if agent:
                if req.description is not None:
                    agent.description = req.description
                if req.descriptionCn is not None:
                    agent.description_cn = req.descriptionCn
                if req.prompt is not None:
                    agent.prompt = req.prompt
                if req.temperature is not None:
                    agent.temperature = req.temperature
                if req.color is not None:
                    agent.color = req.color
                if req.model is not None:
                    agent.model = AgentModelConfig(
                        model_id=req.model.modelID,
                        provider_id=req.model.providerID,
                    )
                if "delegatable" in updates:
                    agent.delegatable = updates["delegatable"]
                if "skills" in updates:
                    agent.skills = updates["skills"]
                if "tools" in updates:
                    agent.tools = updates["tools"]
                if "sub_agents" in updates:
                    agent.sub_agents = updates["sub_agents"]
                if "kb" in updates:
                    agent.kb = updates["kb"]
                overrides = await _load_model_overrides()
                all_tool_names = _get_all_tool_names()
                return await _build_single_agent_response(agent, overrides, all_tool_names)
            updated_yaml_data = read_yaml_agent(name) or {}
            return _custom_agent_data_to_response(updated_yaml_data)

        raise HTTPException(status_code=404, detail=f"Custom agent {name} not found")
    except HTTPException:
        raise
    except Exception as e:
        log.error("agent.update.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{name}", summary="Delete custom agent")
async def delete_agent(name: str):
    """
    Delete a custom agent (Storage-based and/or YAML plugin).

    Cleans up both Storage and YAML sources if they exist.
    Built-in (native) agents cannot be deleted.
    """
    from flocks.storage.storage import Storage

    try:
        deleted_storage = False
        deleted_yaml = False

        # Always try both sources — an agent can have a Storage entry
        # AND a YAML file (e.g. YAML base + Storage overlay from edits).

        agent_key = f"agent/custom/{name}"
        try:
            await Storage.read(agent_key)
            await Storage.remove(agent_key)
            deleted_storage = True
            log.info("agent.deleted", {"name": name, "source": "storage"})
        except Storage.NotFoundError:
            pass

        if delete_yaml_agent(name):
            deleted_yaml = True
            log.info("agent.deleted", {"name": name, "source": "yaml"})

        if not deleted_storage and not deleted_yaml:
            raise HTTPException(
                status_code=404,
                detail=f"Custom agent {name} not found or is a built-in agent",
            )

        from flocks.hub import local as hub_local

        hub_local.remove_installed_record("agent", name)

        # Sync: remove from in-memory agent cache
        from flocks.agent.registry import Agent as AgentRegistry
        AgentRegistry.unregister(name)
        AgentRegistry.invalidate_cache()

        return {"status": "success", "message": f"Agent {name} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        log.error("agent.delete.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{name}/model", response_model=AgentResponse, summary="Update agent model")
async def update_agent_model(name: str, req: AgentModelUpdateRequest):
    """
    Update the model for any agent (native, Storage-based custom, or YAML plugin).

    For native agents: saves a model override to storage (applied at query time).
    For custom/YAML agents: updates the model in the agent config.
    Pass null model to reset to system default.
    """
    from flocks.storage.storage import Storage

    try:
        agent = await Agent.get(name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {name} not found")

        if agent.native:
            async with _get_overrides_lock():
                overrides = await _load_model_overrides()
                entry = overrides.get(name, {})
                if req.model:
                    entry["modelID"] = req.model.modelID
                    entry["providerID"] = req.model.providerID
                else:
                    entry.pop("modelID", None)
                    entry.pop("providerID", None)
                if req.temperature is not None:
                    entry["temperature"] = req.temperature
                if entry:
                    overrides[name] = entry
                else:
                    overrides.pop(name, None)
                await Storage.write("agent/model_overrides", overrides)
            log.info("agent.model_override.saved", {"name": name, "model": req.model, "temperature": req.temperature})
            override = overrides.get(name, {})
            model_override = {k: override[k] for k in ("modelID", "providerID") if k in override} or None
            return agent_to_response(agent, model_override=model_override, temperature_override=override.get("temperature"))
        else:
            # --- Try Storage-based custom agent ---
            agent_key = f"agent/custom/{name}"
            agent_data: Optional[Dict[str, Any]] = None
            try:
                agent_data = await Storage.read(agent_key)
            except Storage.NotFoundError:
                pass

            if agent_data is not None and agent_data.get("name"):
                agent_data["model"] = req.model.model_dump() if req.model else None
                await Storage.write(agent_key, agent_data)

                from flocks.agent.registry import Agent as AgentRegistry
                AgentRegistry.register(name, _agent_data_to_info(agent_data))
                AgentRegistry.invalidate_cache()

                log.info("agent.model.updated", {"name": name, "source": "storage"})
                return _custom_agent_data_to_response(agent_data)

            # --- Fall back to YAML plugin agent ---
            if find_yaml_agent(name) is not None:
                model_update = req.model.model_dump() if req.model else None
                if not update_yaml_agent(name, {"model": model_update}):
                    raise HTTPException(status_code=500, detail=f"Failed to write YAML for agent {name}")

                # Sync in-memory cache
                if req.model:
                    agent.model = AgentModelConfig(
                        model_id=req.model.modelID,
                        provider_id=req.model.providerID,
                    )
                else:
                    agent.model = None

                log.info("agent.model.updated", {"name": name, "source": "yaml"})
                overrides = await _load_model_overrides()
                all_tool_names = _get_all_tool_names()
                return await _build_single_agent_response(agent, overrides, all_tool_names)

            raise HTTPException(status_code=404, detail=f"Custom agent {name} not found")
    except HTTPException:
        raise
    except Exception as e:
        log.error("agent.model.update.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Test route
# =============================================================================

class AgentTestRequest(BaseModel):
    """Request body for testing an agent"""
    test_prompt: str = Field(
        default="Hello, this is a test message.",
        description="Test prompt to send to the agent",
    )


@router.post("/{name}/test", summary="Test agent")
async def test_agent(name: str, req: AgentTestRequest = AgentTestRequest()):
    """
    Test an agent.

    Creates a test session (category="test", hidden from session list), sends
    the prompt, and runs the agent loop asynchronously in the background.

    Returns immediately with the new sessionId so the caller can subscribe to
    the global SSE /api/event stream for live updates (same as normal sessions).

    Response:
      {"sessionId": "...", "status": "started"}
    """
    import os
    import asyncio
    import time as _time

    try:
        agent_info = await Agent.get(name)
        if not agent_info:
            raise HTTPException(status_code=404, detail=f"Agent {name} not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        import os
        from flocks.project.instance import Instance
        from flocks.session.session import Session
        from flocks.session.message import Message, MessageRole
        from flocks.utils.id import Identifier
        from flocks.provider.provider import Provider
        from flocks.tool.registry import ToolRegistry

        # --- 1. resolve directory / project ---
        try:
            directory = Instance.directory
            project_id = Instance.project.id if hasattr(Instance, "project") else "default"
        except Exception:
            directory = os.getcwd()
            project_id = "default"

        # --- 2. create test session ---
        session = await Session.create(
            project_id=project_id,
            directory=directory,
            title=f"[Test] {name}",
            agent=name,
            category="test",
        )
        session_id = session.id

        # --- 3. create user message ---
        now_ms = int(_time.time() * 1000)
        await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content=req.test_prompt,
            id=Identifier.create("message"),
            time={"created": now_ms},
            agent=name,
        )

        # --- 4. init provider / tools ---
        Provider._ensure_initialized()
        ToolRegistry.init()

        # --- 5. run agent loop in background (publishes to global SSE bus) ---
        from flocks.session.session_loop import SessionLoop, LoopCallbacks
        from flocks.server.routes.event import publish_event

        async def _on_error(error: str) -> None:
            await publish_event("session.error", {
                "sessionID": session_id,
                "error": {"name": "TestError", "message": error, "data": {"message": error}},
            })

        loop_cbs = LoopCallbacks(
            on_error=_on_error,
            event_publish_callback=publish_event,
        )

        async def _run_loop() -> None:
            try:
                await SessionLoop.run(
                    session_id=session_id,
                    agent_name=name,
                    callbacks=loop_cbs,
                )
            except Exception as exc:
                log.error("agent.test.loop.error", {"name": name, "error": str(exc)})
                error_msg = str(exc)
                await publish_event("session.error", {
                    "sessionID": session_id,
                    "error": {"name": "LoopError", "message": error_msg, "data": {"message": error_msg}},
                })

        asyncio.create_task(_run_loop())
        log.info("agent.tested", {"name": name, "session_id": session_id})

        return {"sessionId": session_id, "status": "started"}

    except HTTPException:
        raise
    except Exception as e:
        log.error("agent.test.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))
