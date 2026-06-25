"""
Tool routes - API endpoints for tool management and execution
"""

import asyncio
import re
import time
from typing import List, Optional, Dict, Any, Literal, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from flocks.server.auth import require_admin
from flocks.server.routes._timing import log_route_timing
from flocks.utils.log import Log
from flocks.config.config_writer import ConfigWriter
from flocks.permission.next import DeniedError, PermissionNext
from flocks.tool.api_tool_draft import (
    APIToolDraft,
    DraftValidationIssue,
    NonAPIToolDraftResult,
    compile_provider_yaml,
    compile_tool_yaml,
    has_validation_errors,
    normalize_api_tool_draft,
    validate_api_tool_draft,
)
from flocks.tool.api_tool_draft_sources import APIToolDraftSource, extract_and_merge_sources
from flocks.tool.registry import (
    ToolRegistry,
    ToolInfo,
    ToolSchema,
    ToolResult,
    ToolCategory,
    ToolContext,
)


router = APIRouter()
log = Log.create(service="tool-routes")
_SECRET_REF_PATTERN = re.compile(r"\{secret:([^}]+)\}")


# Request/Response Models

class ToolInfoResponse(BaseModel):
    """Tool information response"""
    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    description_cn: Optional[str] = Field(None, description="Chinese UI description")
    category: str = Field(..., description="Tool category")
    source: str = Field("builtin", description="Tool source: builtin, mcp, api, custom")
    source_name: Optional[str] = Field(None, description="Source detail, e.g. MCP server name or API module name")
    parameters: List[Dict[str, Any]] = Field(default_factory=list, description="Tool parameters")
    enabled: bool = Field(True, description="Effective enabled state (overlay applied, ANDed with API service flag)")
    enabled_default: bool = Field(True, description="Factory default from the YAML/registration source (no overlay)")
    enabled_customized: bool = Field(False, description="True if a user setting is recorded in flocks.json tool_settings")
    requires_confirmation: bool = Field(False, description="Requires confirmation")


class ToolSchemaResponse(BaseModel):
    """Tool schema response"""
    name: str = Field(..., description="Tool name")
    schema_: Dict[str, Any] = Field(..., alias="schema", description="JSON Schema")


class ToolUpdateRequest(BaseModel):
    """Tool update request"""
    enabled: bool = Field(..., description="Enable or disable the tool")


class ToolExecuteRequest(BaseModel):
    """Tool execution request"""
    model_config = {"populate_by_name": True}
    params: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")
    session_id: Optional[str] = Field(
        None,
        alias="sessionID",
        description="Optional session ID used for permission-gated execution",
    )
    message_id: Optional[str] = Field(
        None,
        alias="messageID",
        description="Optional message ID used for permission-gated execution",
    )
    agent: Optional[str] = Field(
        "rex",
        description="Agent name recorded for the execution context",
    )


class ToolExecuteResponse(BaseModel):
    """Tool execution response"""
    success: bool = Field(..., description="Execution successful")
    output: Any = Field(None, description="Output data")
    error: Optional[str] = Field(None, description="Error message")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata")


class BatchToolCall(BaseModel):
    """Single tool call in batch"""
    name: str = Field(..., description="Tool name")
    params: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


class BatchExecuteRequest(BaseModel):
    """Batch tool execution request"""
    model_config = {"populate_by_name": True}
    calls: List[BatchToolCall] = Field(..., description="Tool calls to execute")
    parallel: bool = Field(True, description="Execute in parallel")
    session_id: Optional[str] = Field(
        None,
        alias="sessionID",
        description="Optional session ID used for permission-gated execution",
    )
    message_id: Optional[str] = Field(
        None,
        alias="messageID",
        description="Optional message ID used for permission-gated execution",
    )
    agent: Optional[str] = Field(
        "rex",
        description="Agent name recorded for the execution context",
    )


class BatchExecuteResponse(BaseModel):
    """Batch tool execution response"""
    results: List[ToolExecuteResponse] = Field(..., description="Execution results")


# Helper: determine tool source

_BUILTIN_CATEGORIES = {
    ToolCategory.FILE, ToolCategory.TERMINAL, ToolCategory.BROWSER,
    ToolCategory.CODE, ToolCategory.SEARCH, ToolCategory.SYSTEM,
}

_DIRECT_HTTP_BLOCKED_MESSAGE = (
    "Direct HTTP tool execution is disabled for local or permission-gated tools. "
    "Use a session-backed request (provide sessionID/messageID) or run the tool via the normal agent/session flow."
)
_VERIFIED_CONTEXT_REQUIRED_MESSAGE = (
    "Direct HTTP execution for local or permission-gated tools requires a verified "
    "session-backed request with both sessionID and messageID."
)


def _get_tool_source(tool_info: ToolInfo) -> tuple:
    """
    Determine tool source type and source name.
    
    Returns:
        (source, source_name) tuple where source is one of:
        'builtin', 'mcp', 'api', 'plugin_yaml', 'plugin_py', 'custom'
    """
    # Use ToolInfo.source field if explicitly set
    if tool_info.source == "api":
        return "api", tool_info.provider
    if tool_info.source == "plugin_yaml":
        return "plugin_yaml", tool_info.provider
    if tool_info.source == "plugin_py":
        return "plugin_py", None

    # Check MCP source
    try:
        from flocks.mcp import MCP
        if MCP.is_mcp_tool(tool_info.name):
            source_info = MCP.get_tool_source(tool_info.name)
            server_name = source_info.mcp_server if source_info else None
            return "mcp", server_name
    except Exception as e:
        log.debug("tool.source_check.mcp_error", {"tool": tool_info.name, "error": str(e)})
    
    # Check if from dynamic/generated module (API tools)
    for module_name, tool_names in ToolRegistry.get_dynamic_tools_by_module().items():
        if tool_info.name in tool_names:
            friendly_name = module_name.rsplit(".", 1)[-1] if "." in module_name else module_name
            return "api", friendly_name
    
    # Builtin tools: recognized by non-CUSTOM categories
    if tool_info.category in _BUILTIN_CATEGORIES:
        return "builtin", "Flocks"
    
    # Default: custom
    return "custom", None


def _build_tool_response(t: ToolInfo) -> ToolInfoResponse:
    """Build ToolInfoResponse with source info and overlay metadata."""
    source, source_name = _get_tool_source(t)
    setting = ConfigWriter.get_tool_setting(t.name) or {}
    customized = "enabled" in setting
    enabled_default = _get_default_enabled(t)
    return ToolInfoResponse(
        name=t.name,
        description=t.description,
        description_cn=t.description_cn,
        category=t.category.value,
        source=source,
        source_name=source_name,
        parameters=[p.model_dump() for p in t.parameters],
        enabled=_get_effective_tool_enabled(t),
        enabled_default=enabled_default,
        enabled_customized=customized,
        requires_confirmation=t.requires_confirmation,
    )


def _requires_session_backed_context(tool_info: ToolInfo) -> bool:
    """Return True when a tool must be anchored to a real session/message context."""
    source, _ = _get_tool_source(tool_info)
    return source == "builtin"


async def _validate_verified_session_message_context(
    *,
    requires_verified_context: bool,
    session_id: Optional[str],
    message_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Validate that session/message context exists and the message belongs to the session."""
    effective_session_id = str(session_id or "").strip() or None
    effective_message_id = str(message_id or "").strip() or None
    needs_verified_context = requires_verified_context or bool(effective_session_id or effective_message_id)

    if not needs_verified_context:
        return None, None

    if not effective_session_id or not effective_message_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_VERIFIED_CONTEXT_REQUIRED_MESSAGE,
        )

    from flocks.session.message import Message
    from flocks.session.session import Session

    session = await Session.get_by_id(effective_session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {effective_session_id}",
        )

    message = await Message.get(effective_session_id, effective_message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {effective_message_id} not found in session {effective_session_id}",
        )

    return effective_session_id, effective_message_id


async def _validate_session_message_context(
    *,
    tool_info: ToolInfo,
    session_id: Optional[str],
    message_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Validate context for a single tool execution request."""
    return await _validate_verified_session_message_context(
        requires_verified_context=_requires_session_backed_context(tool_info),
        session_id=session_id,
        message_id=message_id,
    )


async def _build_http_tool_context(
    *,
    tool_name: str,
    tool_info: ToolInfo,
    session_id: Optional[str],
    message_id: Optional[str],
    agent: Optional[str],
) -> ToolContext:
    """Create a safe ToolContext for HTTP-triggered execution."""
    agent_name = agent or "rex"
    effective_message_id = message_id or f"http-tool:{tool_name}"

    if session_id:
        from flocks.session.session import Session

        output_session_id = await Session.resolve_root_session_id(session_id)

        async def permission_callback(request) -> None:
            metadata = dict(request.metadata or {})
            metadata.setdefault("messageID", effective_message_id)
            metadata.setdefault("route", "tool.execute")
            await PermissionNext.ask(
                session_id=session_id,
                permission=request.permission,
                patterns=list(request.patterns or []),
                ruleset=[],
                metadata=metadata,
                always=list(request.always or []),
                tool={"name": tool_name},
            )

        extra: Dict[str, Any] = {}
        try:
            from flocks.session.session import Session

            session = await Session.get_by_id(session_id)
            if session:
                extra["session_category"] = getattr(session, "category", None)
                session_metadata = getattr(session, "metadata", None)
                if isinstance(session_metadata, dict):
                    extra["session_metadata"] = dict(session_metadata)
                    loaded_skills = session_metadata.get("loadedSkills")
                    if isinstance(loaded_skills, list):
                        extra["loadedSkills"] = list(loaded_skills)
        except Exception as exc:
            log.warn("tool.context.session_metadata_failed", {"session_id": session_id, "error": str(exc)})

        # 将第二段代码中需要的新增字段补充进 extra 字典中
        extra["main_session_key"] = output_session_id
        extra["output_session_id"] = output_session_id

        return ToolContext(
            session_id=session_id,
            message_id=effective_message_id,
            agent=agent_name,
            permission_callback=permission_callback,
            extra=extra,
        )

    if _requires_session_backed_context(tool_info):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_DIRECT_HTTP_BLOCKED_MESSAGE,
        )

    async def deny_permission_callback(request) -> None:
        raise PermissionError(
            "This HTTP execution context cannot auto-approve tool permissions."
        )

    return ToolContext(
        session_id="http-tool",
        message_id=effective_message_id,
        agent=agent_name,
        permission_callback=deny_permission_callback,
    )


def _permission_denied_http_error(exc: Exception) -> HTTPException:
    """Normalize permission failures into a consistent 403 response."""
    detail = str(exc).strip() or _DIRECT_HTTP_BLOCKED_MESSAGE
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def _execute_with_http_context(
    *,
    tool_name: str,
    tool_info: ToolInfo,
    params: Dict[str, Any],
    session_id: Optional[str],
    message_id: Optional[str],
    agent: Optional[str],
) -> ToolResult:
    """Execute a tool using an HTTP-safe ToolContext."""
    validated_session_id, validated_message_id = await _validate_session_message_context(
        tool_info=tool_info,
        session_id=session_id,
        message_id=message_id,
    )
    ctx = await _build_http_tool_context(
        tool_name=tool_name,
        tool_info=tool_info,
        session_id=validated_session_id,
        message_id=validated_message_id,
        agent=agent,
    )
    try:
        return await ToolRegistry.execute(tool_name=tool_name, ctx=ctx, **params)
    except (DeniedError, PermissionError) as exc:
        raise _permission_denied_http_error(exc) from exc


async def _execute_batch_with_http_context(
    *,
    calls: List[BatchToolCall],
    session_id: Optional[str],
    message_id: Optional[str],
    agent: Optional[str],
    parallel: bool,
) -> List[ToolResult]:
    """Execute batch calls with a per-tool HTTP context."""

    async def run_call(call: BatchToolCall) -> ToolResult:
        tool = ToolRegistry.get(call.name)
        if tool is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tool not found: {call.name}",
            )
        return await _execute_with_http_context(
            tool_name=call.name,
            tool_info=tool.info,
            params=call.params,
            session_id=session_id,
            message_id=message_id,
            agent=agent,
        )

    if parallel:
        return await asyncio.gather(*(run_call(call) for call in calls))

    results: List[ToolResult] = []
    for call in calls:
        results.append(await run_call(call))
    return results

def _get_default_enabled(t: ToolInfo) -> bool:
    """Return the registration-time default for ``enabled``.

    Prefers :meth:`ToolRegistry.get_default_enabled` (a snapshot taken
    before sync/overlay mutate ``info.enabled`` in place).  Falls back to
    the YAML file when the snapshot is missing (e.g. a tool registered
    after init), then to the live value as the very last resort.
    """
    snapshot = ToolRegistry.get_default_enabled(t.name)
    if snapshot is not None:
        return snapshot
    try:
        from flocks.tool.tool_loader import read_yaml_tool
        raw = read_yaml_tool(t.name)
    except Exception:
        raw = None
    if isinstance(raw, dict) and "enabled" in raw:
        return bool(raw["enabled"])
    return t.enabled


def _service_allows_enable(t: ToolInfo) -> bool:
    """Return True when the API service backing ``t`` (if any) is enabled.

    Mirrors the gate in :meth:`ToolRegistry._apply_tool_settings` so that
    HTTP mutations stay consistent with what the registry would compute
    on its next reload: an overlay can never *open* a tool whose service
    is currently disabled.
    """
    if not t.provider:
        return True
    svc = ConfigWriter.get_api_service_raw(t.provider) or {}
    return bool(svc.get("enabled", False))


def _get_effective_tool_enabled(tool_info: ToolInfo) -> bool:
    """Compute tool enabled state without mutating the registry object."""
    source, source_name = _get_tool_source(tool_info)
    if source != "api" or not source_name:
        return tool_info.enabled
    from flocks.server.routes.provider import _get_api_service_enabled

    return tool_info.enabled and _get_api_service_enabled(source_name)


# Routes

@router.get(
    "",
    response_model=List[ToolInfoResponse],
    summary="List all tools",
)
async def list_tools(
    category: Optional[str] = None,
    source: Optional[str] = None,
):
    """
    List all available tools
    
    Args:
        category: Optional category filter (file, terminal, browser, etc.)
        source: Optional source filter (builtin, mcp, api, custom)
        
    Returns:
        List of tool information
    """
    # Initialize registry if needed
    started_at = time.perf_counter()
    ToolRegistry.init()
    
    # Parse category filter
    cat_filter = None
    if category:
        try:
            cat_filter = ToolCategory(category)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category: {category}"
            )
    
    tools = ToolRegistry.list_tools(category=cat_filter)
    result = [_build_tool_response(t) for t in tools]
    
    # Apply source filter if specified
    if source:
        result = [t for t in result if t.source == source]

    log_route_timing(log, "tools.list.complete", started_at=started_at, extra={
        "count": len(result),
        "category": category,
        "source": source,
    })
    return result


@router.get(
    "/{tool_name}",
    response_model=ToolInfoResponse,
    summary="Get tool details",
)
async def get_tool(tool_name: str):
    """
    Get tool information by name
    
    Args:
        tool_name: Tool name
        
    Returns:
        Tool information
    """
    ToolRegistry.init()
    
    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}"
        )

    return _build_tool_response(tool.info)


@router.patch(
    "/{tool_name}",
    response_model=ToolInfoResponse,
    summary="Update tool settings",
)
async def update_tool(tool_name: str, request: ToolUpdateRequest, _admin: object = Depends(require_admin)):
    """
    Update tool settings (e.g., enable or disable).

    The ``enabled`` flag is persisted to the user-level overlay in
    ``flocks.json`` (``tool_settings.<tool_name>.enabled``) instead of
    mutating the YAML plugin file.  This keeps project-level YAML files
    (which may be tracked by git and overwritten on upgrade) clean and
    treats the YAML's ``enabled:`` field as the factory default that the
    overlay can selectively customise.

    Two behaviours of note:

    * If ``request.enabled`` matches the registration-time default we
      *delete* the overlay entry instead of writing one — the tool is
      back to "no customisation", and the UI's "已自定义" badge clears.
    * Asking to enable a tool whose API service is currently disabled
      still persists the overlay (so the intent survives the service
      being re-enabled later) but does not flip the in-memory
      ``info.enabled`` flag, mirroring the gate in
      :meth:`ToolRegistry._apply_tool_settings`.
    """
    ToolRegistry.init()

    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}",
        )

    desired = bool(request.enabled)
    default = _get_default_enabled(tool.info)
    # Service gate: only matters when the user is trying to enable.
    # Disabling is always honoured.
    service_ok = _service_allows_enable(tool.info)
    new_enabled = desired and service_ok

    if desired == default:
        removed = ConfigWriter.delete_tool_setting(tool_name)
        log.info("tool.updated.reset_to_default", {
            "name": tool_name,
            "enabled": new_enabled,
            "default": default,
            "removed_overlay": removed,
        })
    else:
        ConfigWriter.set_tool_setting(tool_name, {"enabled": desired})
        log.info("tool.updated", {
            "name": tool_name,
            "enabled": new_enabled,
            "requested": desired,
            "blocked_by_service": desired and not service_ok,
            "native": tool.info.native,
            "store": "overlay",
        })

    tool.info.enabled = new_enabled
    return _build_tool_response(tool.info)


@router.post(
    "/{tool_name}/reset",
    response_model=ToolInfoResponse,
    summary="Reset a tool to its YAML/registration default",
)
async def reset_tool_setting(tool_name: str, _admin: object = Depends(require_admin)):
    """Remove the user setting for ``tool_name`` and restore the default.

    Restores the registration-time ``enabled`` value from the registry's
    snapshot (or the YAML file as a fallback) and re-applies the same
    service gate as :meth:`ToolRegistry._apply_tool_settings`, so the
    HTTP layer never leaves the in-memory state in a position the
    registry would refuse on its next reload.
    """
    ToolRegistry.init()

    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}",
        )

    removed = ConfigWriter.delete_tool_setting(tool_name)
    default = _get_default_enabled(tool.info)
    new_enabled = default and _service_allows_enable(tool.info)
    tool.info.enabled = new_enabled

    log.info("tool.setting.reset", {
        "name": tool_name,
        "removed": removed,
        "default": default,
        "restored_enabled": new_enabled,
    })
    return _build_tool_response(tool.info)


@router.get(
    "/{tool_name}/schema",
    response_model=ToolSchemaResponse,
    summary="Get tool schema",
)
async def get_tool_schema(tool_name: str):
    """
    Get JSON Schema for a tool
    
    Args:
        tool_name: Tool name
        
    Returns:
        Tool JSON Schema
    """
    ToolRegistry.init()
    
    schema = ToolRegistry.get_schema(tool_name)
    if not schema:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}"
        )
    
    return ToolSchemaResponse(
        name=tool_name,
        schema=schema.to_json_schema(),
    )


@router.post(
    "/{tool_name}/execute",
    response_model=ToolExecuteResponse,
    summary="Execute a tool",
)
async def execute_tool(tool_name: str, request: ToolExecuteRequest):
    """
    Execute a tool with given parameters
    
    Args:
        tool_name: Tool name
        request: Execution parameters
        
    Returns:
        Execution result
    """
    ToolRegistry.init()
    
    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {tool_name}"
        )

    if not _get_effective_tool_enabled(tool.info):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tool is disabled: {tool_name}"
        )
    
    log.info("tool.execute.request", {
        "tool": tool_name,
        "params": list(request.params.keys()),
        "session": request.session_id,
    })

    result = await _execute_with_http_context(
        tool_name=tool_name,
        tool_info=tool.info,
        params=request.params,
        session_id=request.session_id,
        message_id=request.message_id,
        agent=request.agent,
    )
    
    return ToolExecuteResponse(
        success=result.success,
        output=result.output,
        error=result.error,
        metadata=result.metadata,
    )


@router.post(
    "/batch",
    response_model=BatchExecuteResponse,
    summary="Execute multiple tools",
)
async def execute_batch(request: BatchExecuteRequest):
    """
    Execute multiple tools in batch
    
    Args:
        request: Batch execution request
        
    Returns:
        List of execution results
    """
    ToolRegistry.init()
    
    # Validate all tools exist
    for call in request.calls:
        tool = ToolRegistry.get(call.name)
        if not tool:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tool not found: {call.name}"
            )
    
    log.info("tool.batch.request", {
        "count": len(request.calls),
        "parallel": request.parallel,
        "session": request.session_id,
    })

    requires_verified_context = False
    for call in request.calls:
        tool = ToolRegistry.get(call.name)
        if tool and _requires_session_backed_context(tool.info):
            requires_verified_context = True
            break

    validated_session_id, validated_message_id = await _validate_verified_session_message_context(
        requires_verified_context=requires_verified_context,
        session_id=request.session_id,
        message_id=request.message_id,
    )

    try:
        results = await _execute_batch_with_http_context(
            calls=request.calls,
            session_id=validated_session_id,
            message_id=validated_message_id,
            agent=request.agent,
            parallel=request.parallel,
        )
    except (DeniedError, PermissionError) as exc:
        raise _permission_denied_http_error(exc) from exc
    
    return BatchExecuteResponse(
        results=[
            ToolExecuteResponse(
                success=r.success,
                output=r.output,
                error=r.error,
                metadata=r.metadata,
            )
            for r in results
        ]
    )


class RefreshResponse(BaseModel):
    """Tool refresh response"""
    status: str = Field(..., description="Operation status")
    tool_count: int = Field(..., description="Total registered tool count after refresh")
    message: str = Field("", description="Human-readable summary")


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="Refresh all plugin and dynamic tools",
)
async def refresh_tools(_admin: object = Depends(require_admin)):
    """
    Reload all plugin tools (YAML + Python) and dynamically generated tools
    from disk without restarting the service.

    This is the batch counterpart to the single-tool ``/{name}/reload`` endpoint.
    """
    started_at = time.perf_counter()
    ToolRegistry.init()

    errors: list[str] = []

    # 1. Reload generated tools (generated/)
    try:
        ToolRegistry.refresh_dynamic_tools()
    except Exception as e:
        log.error("tools.refresh.dynamic_error", {"error": str(e)})
        errors.append(f"dynamic: {e}")

    # 2. Reload plugin tools (api/, python/) — unregisters stale entries first
    try:
        ToolRegistry.refresh_plugin_tools()
    except Exception as e:
        log.error("tools.refresh.plugin_error", {"error": str(e)})
        errors.append(f"plugin: {e}")

    tool_count = len(ToolRegistry.all_tool_ids())
    log_route_timing(log, "tools.refresh.done", started_at=started_at, extra={
        "tool_count": tool_count,
        "errors": len(errors),
    })

    if errors:
        return RefreshResponse(
            status="partial",
            tool_count=tool_count,
            message=f"Refreshed with {len(errors)} error(s): {'; '.join(errors)}",
        )

    return RefreshResponse(
        status="success",
        tool_count=tool_count,
        message=f"All tools refreshed successfully ({tool_count} tools registered)",
    )


# =============================================================================
# WebUI Enhancement Routes
# =============================================================================

class ToolTestRequest(BaseModel):
    """Request to test a tool"""
    model_config = {"populate_by_name": True}
    params: Dict[str, Any] = Field(default_factory=dict, description="Test parameters")
    session_id: Optional[str] = Field(
        None,
        alias="sessionID",
        description="Optional session ID used for permission-gated execution",
    )
    message_id: Optional[str] = Field(
        None,
        alias="messageID",
        description="Optional message ID used for permission-gated execution",
    )
    agent: Optional[str] = Field(
        "rex",
        description="Agent name recorded for the execution context",
    )


@router.post(
    "/{name}/test",
    response_model=ToolExecuteResponse,
    summary="Test tool",
)
async def test_tool(name: str, request: ToolTestRequest):
    """
    Test a tool
    
    Executes the tool with provided test parameters and returns the result.
    """
    ToolRegistry.init()
    
    tool = ToolRegistry.get(name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool not found: {name}"
        )
    
    log.info("tool.test", {"name": name, "params": request.params})

    tool_request = ToolExecuteRequest(
        params=request.params,
        sessionID=request.session_id,
        messageID=request.message_id,
        agent=request.agent,
    )

    # Execute tool
    try:
        result = await _execute_with_http_context(
            tool_name=name,
            tool_info=tool.info,
            params=tool_request.params,
            session_id=tool_request.session_id,
            message_id=tool_request.message_id,
            agent=tool_request.agent,
        )
        return ToolExecuteResponse(
            success=result.success,
            output=result.output,
            error=result.error,
            metadata=result.metadata,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("tool.test.error", {"name": name, "error": str(e)})
        return ToolExecuteResponse(
            success=False,
            output=None,
            error=str(e),
            metadata={},
        )


# =============================================================================
# Test Fixtures Route
# =============================================================================


class FixtureItemResponse(BaseModel):
    """One predeclared test sample for a tool."""
    label: str = Field(..., description="Default (English) sample name for the UI drop-down")
    label_cn: Optional[str] = Field(None, description="Optional Chinese override; WebUI picks it when locale is zh-*")
    params: Dict[str, Any] = Field(default_factory=dict, description="Call params, verbatim")
    tags: List[str] = Field(default_factory=list, description="Semantic labels (smoke, ip, …)")
    has_assertion: bool = Field(False, description="Whether the sample declares a pass/fail assertion")


@router.get(
    "/{name}/fixtures",
    response_model=List[FixtureItemResponse],
    summary="List declared test fixtures for a tool",
)
async def list_tool_fixtures(name: str) -> List[FixtureItemResponse]:
    """Return predeclared test samples sourced from the tool's provider ``_test.yaml``.

    Returns an empty list when no manifest exists or no fixtures are declared.
    """
    from flocks.tool.probe_loader import get_tool_fixtures_by_tool_name

    return [
        FixtureItemResponse(
            label=f.label,
            label_cn=f.label_cn,
            params=f.params,
            tags=list(f.tags),
            has_assertion=bool(f.assertion),
        )
        for f in get_tool_fixtures_by_tool_name(name)
    ]


# =============================================================================
# Plugin Tool CRUD Routes
# =============================================================================

class CreateToolRequest(BaseModel):
    """Request to create a YAML plugin tool"""
    name: str = Field(..., description="Tool name (snake_case)")
    description: str = Field("", description="Tool description")
    category: str = Field("custom", description="Tool category")
    provider: Optional[str] = Field(None, description="Provider name for grouping")
    enabled: bool = Field(True, description="Is tool enabled")
    requires_confirmation: bool = Field(False, description="Requires user confirmation")
    inputSchema: Optional[Dict[str, Any]] = Field(None, description="MCP-compatible JSON Schema")
    parameters: Optional[List[Dict[str, Any]]] = Field(None, description="Simplified parameter list")
    handler: Dict[str, Any] = Field(..., description="Handler config (type: http|script)")
    response: Optional[Dict[str, Any]] = Field(None, description="Response processing config")


class UpdateToolRequest(BaseModel):
    """Request to update a YAML plugin tool"""
    description: Optional[str] = Field(None)
    category: Optional[str] = Field(None)
    enabled: Optional[bool] = Field(None)
    requires_confirmation: Optional[bool] = Field(None)
    inputSchema: Optional[Dict[str, Any]] = Field(None)
    parameters: Optional[List[Dict[str, Any]]] = Field(None)
    handler: Optional[Dict[str, Any]] = Field(None)
    response: Optional[Dict[str, Any]] = Field(None)


class GenerateAPIToolDraftRequest(BaseModel):
    sources: List[APIToolDraftSource] = Field(default_factory=list)
    auth_hint: Optional[Dict[str, Any]] = None
    tool_name_prefix: Optional[str] = None
    provider_id: Optional[str] = None
    model_id: Optional[str] = None


class GenerateAPIToolDraftResponse(BaseModel):
    draft: Union[APIToolDraft, NonAPIToolDraftResult]
    issues: List[DraftValidationIssue] = Field(default_factory=list)


class ValidateAPIToolDraftRequest(BaseModel):
    draft: APIToolDraft
    check_collisions: bool = True


class ValidateAPIToolDraftResponse(BaseModel):
    draft: APIToolDraft
    issues: List[DraftValidationIssue] = Field(default_factory=list)
    valid: bool = True


class ConfirmAPIToolDraftRequest(BaseModel):
    draft: APIToolDraft
    mode: Literal["create", "upsert"] = "create"
    delete_missing_tools: bool = True


class ConfirmAPIToolDraftResponse(BaseModel):
    provider_path: str
    tool_paths: List[str] = Field(default_factory=list)
    tools: List[ToolInfoResponse] = Field(default_factory=list)
    issues: List[DraftValidationIssue] = Field(default_factory=list)


class DeleteAPIToolProviderResponse(BaseModel):
    success: bool = True
    provider_id: str
    provider_path: Optional[str] = None
    removed_provider: bool = False
    removed_tools: List[str] = Field(default_factory=list)
    removed_config_keys: List[str] = Field(default_factory=list)
    deleted_secrets: List[str] = Field(default_factory=list)


class PluginToolListResponse(BaseModel):
    """Response listing YAML plugin tools"""
    tools: List[Dict[str, Any]] = Field(default_factory=list)


def _collect_secret_ids_from_value(value: Any, secret_ids: set[str]) -> None:
    if isinstance(value, str):
        secret_ids.update(item.strip() for item in _SECRET_REF_PATTERN.findall(value) if item.strip())
    elif isinstance(value, dict):
        for item in value.values():
            _collect_secret_ids_from_value(item, secret_ids)
    elif isinstance(value, list):
        for item in value:
            _collect_secret_ids_from_value(item, secret_ids)


def _collect_api_provider_secret_ids(provider_yaml: Dict[str, Any], config_values: List[Dict[str, Any]]) -> List[str]:
    secret_ids: set[str] = set()
    credential_fields = provider_yaml.get("credential_fields")
    if isinstance(credential_fields, list):
        for field in credential_fields:
            if not isinstance(field, dict):
                continue
            secret_id = field.get("secret_id")
            if field.get("storage") == "secret" and isinstance(secret_id, str) and secret_id.strip():
                secret_ids.add(secret_id.strip())
    _collect_secret_ids_from_value(provider_yaml, secret_ids)
    for config_value in config_values:
        _collect_secret_ids_from_value(config_value, secret_ids)
    return sorted(secret_ids)


def _snapshot_text_file(path) -> Optional[str]:
    try:
        if path is not None and path.is_file():
            return path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("tool.yaml.snapshot_failed", {"path": str(path), "error": str(e)})
    return None


def _restore_text_file(path, previous_content: Optional[str], *, name: str) -> None:
    try:
        if previous_content is None:
            if path is not None and path.is_file():
                path.unlink()
            if path is not None:
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(previous_content, encoding="utf-8")
    except Exception as e:
        log.warning("tool.yaml.rollback_failed", {"name": name, "path": str(path), "error": str(e)})


def _remove_plugin_tool_tracking(name: str) -> None:
    ToolRegistry.unregister(name)
    while name in ToolRegistry._plugin_tool_names:
        ToolRegistry._plugin_tool_names.remove(name)


async def _create_and_register_yaml_tool(
    data: Dict[str, Any],
    *,
    provider: Optional[str],
    enabled: bool,
    overwrite: bool = False,
):
    from flocks.tool.tool_loader import (
        TOOL_TYPE_API,
        find_api_provider_tool,
        find_yaml_tool,
        upsert_yaml_tool,
        yaml_to_tool,
    )

    ToolRegistry.init()

    tool_name = str(data.get("name") or "")
    existing_path = None
    if tool_name:
        if provider:
            existing_path = find_api_provider_tool(provider, tool_name)
        if existing_path is None:
            existing_path = find_yaml_tool(tool_name)
    previous_content = _snapshot_text_file(existing_path)

    try:
        yaml_path = upsert_yaml_tool(data, provider=provider, tool_type=TOOL_TYPE_API, overwrite=overwrite)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        log.error("tool.create.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))

    try:
        tool = yaml_to_tool(data, yaml_path)
        if not tool.info.source:
            tool.info.source = "plugin_yaml"
        if provider:
            tool.info.provider = provider
        ToolRegistry.register(tool)
        if tool.info.name not in ToolRegistry._plugin_tool_names:
            ToolRegistry._plugin_tool_names.append(tool.info.name)
    except Exception as e:
        log.error("tool.create.register_error", {"error": str(e), "name": data.get("name")})
        _restore_text_file(yaml_path, previous_content, name=tool_name or str(data.get("name") or "unknown"))
        if tool_name:
            _remove_plugin_tool_tracking(tool_name)
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY if isinstance(e, ValueError) else 500
        detail = (
            f"Tool config is invalid: {e}"
            if status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            else f"Tool file rolled back after registration failure: {e}"
        )
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        )

    if provider and enabled:
        from flocks.server.routes.provider import (
            APIServiceUpdateRequest,
            update_api_service,
        )

        await update_api_service(
            provider,
            APIServiceUpdateRequest(enabled=True),
        )

    return tool, yaml_path


@router.post(
    "/drafts",
    response_model=GenerateAPIToolDraftResponse,
    summary="Generate an editable API tool draft",
)
async def generate_api_tool_draft_route(
    request: GenerateAPIToolDraftRequest,
    _admin: object = Depends(require_admin),
):
    if not request.sources:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sources is required")

    try:
        source_context, _extracted, _source_warnings = extract_and_merge_sources(request.sources)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        from flocks.tool.api_tool_draft_llm import generate_api_tool_draft

        result = await generate_api_tool_draft(
            source_context=source_context,
            auth_hint=request.auth_hint,
            tool_name_prefix=request.tool_name_prefix,
            model_id=request.model_id,
        )
    except ValueError as e:
        message = str(e)
        code = status.HTTP_400_BAD_REQUEST if "not configured" in message else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=message)
    except Exception as e:
        log.error("tool.draft.generate_error", {"error": str(e)})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM draft generation failed: {e}")

    if not result.is_api_related:
        return GenerateAPIToolDraftResponse(
            draft=result,
            issues=[],
        )

    if not isinstance(result, APIToolDraft):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM draft generation did not return a draft")
    if request.provider_id:
        result.provider.id = request.provider_id
    draft = normalize_api_tool_draft(result)
    issues = validate_api_tool_draft(draft)
    return GenerateAPIToolDraftResponse(
        draft=draft,
        issues=issues,
    )


@router.post(
    "/drafts/validate",
    response_model=ValidateAPIToolDraftResponse,
    summary="Validate an editable API tool draft without writing files",
)
async def validate_api_tool_draft_route(
    request: ValidateAPIToolDraftRequest,
    _admin: object = Depends(require_admin),
):
    draft = normalize_api_tool_draft(request.draft)
    issues = validate_api_tool_draft(draft, check_collisions=request.check_collisions)
    return ValidateAPIToolDraftResponse(
        draft=draft,
        issues=issues,
        valid=not has_validation_errors(issues),
    )


@router.post(
    "/drafts/confirm",
    response_model=ConfirmAPIToolDraftResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm an API tool draft and create YAML tool files",
)
async def confirm_api_tool_draft_route(
    request: ConfirmAPIToolDraftRequest,
    _admin: object = Depends(require_admin),
):
    from flocks.tool.tool_loader import (
        create_api_provider_yaml,
        delete_api_provider_tool,
        find_api_provider_dir,
        find_api_provider_tool,
        find_yaml_tool,
        list_api_provider_tools,
    )

    draft = normalize_api_tool_draft(request.draft)
    upsert = request.mode == "upsert"
    delete_missing_tools = request.delete_missing_tools if upsert else False
    issues = validate_api_tool_draft(draft, check_collisions=not upsert)
    if has_validation_errors(issues):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[issue.model_dump() for issue in issues],
        )

    if upsert:
        for tool_draft in draft.tools:
            existing_path = find_yaml_tool(tool_draft.name)
            if existing_path is None:
                continue
            provider_path = find_api_provider_tool(draft.provider.id, tool_draft.name)
            same_provider_tool = provider_path is not None and existing_path.resolve() == provider_path.resolve()
            if not same_provider_tool:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Tool '{tool_draft.name}' already exists outside provider '{draft.provider.id}'",
                )

    existing_provider_dir = find_api_provider_dir(draft.provider.id)
    existing_provider_path = existing_provider_dir / "_provider.yaml" if existing_provider_dir is not None else None
    previous_provider_content = _snapshot_text_file(existing_provider_path)

    provider_yaml = compile_provider_yaml(draft.provider)
    try:
        provider_path = create_api_provider_yaml(
            draft.provider.id,
            provider_yaml,
            overwrite=upsert,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        log.error("tool.draft.provider_write_error", {"error": str(e), "provider": draft.provider.id})
        raise HTTPException(status_code=500, detail=str(e))

    tool_paths: List[str] = []
    responses: List[ToolInfoResponse] = []
    written_tool_snapshots: List[tuple[str, Any, Optional[str]]] = []
    draft_tool_names = {tool.name for tool in draft.tools}
    try:
        for tool_draft in draft.tools:
            existing_tool_path = find_api_provider_tool(draft.provider.id, tool_draft.name)
            if existing_tool_path is None:
                existing_tool_path = find_yaml_tool(tool_draft.name)
            previous_tool_content = _snapshot_text_file(existing_tool_path)

            tool_data = compile_tool_yaml(tool_draft)
            tool, yaml_path = await _create_and_register_yaml_tool(
                tool_data,
                provider=draft.provider.id,
                enabled=tool_draft.enabled,
                overwrite=upsert,
            )
            written_tool_snapshots.append((tool_draft.name, yaml_path, previous_tool_content))
            tool_paths.append(str(yaml_path))
            responses.append(_build_tool_response(tool.info))

        deleted_missing = False
        if delete_missing_tools:
            for existing_tool_path in list_api_provider_tools(draft.provider.id):
                tool_name = existing_tool_path.stem
                if tool_name not in draft_tool_names:
                    deleted_missing = delete_api_provider_tool(draft.provider.id, tool_name) or deleted_missing
            if deleted_missing:
                ToolRegistry.refresh_plugin_tools()
    except Exception:
        for tool_name, yaml_path, previous_content in reversed(written_tool_snapshots):
            _restore_text_file(yaml_path, previous_content, name=tool_name)
            _remove_plugin_tool_tracking(tool_name)
        _restore_text_file(provider_path, previous_provider_content, name=draft.provider.id)
        try:
            ToolRegistry.refresh_plugin_tools()
        except Exception as e:
            log.warning("tool.draft.rollback_refresh_failed", {"provider": draft.provider.id, "error": str(e)})
        raise

    return ConfirmAPIToolDraftResponse(
        provider_path=str(provider_path),
        tool_paths=tool_paths,
        tools=responses,
        issues=issues,
    )


@router.delete(
    "/drafts/{provider_id}",
    response_model=DeleteAPIToolProviderResponse,
    summary="Delete an API tool provider draft service",
)
async def delete_api_tool_provider_route(
    provider_id: str,
    delete_secrets: bool = Query(False, description="Delete secrets referenced by provider credential_fields/config"),
    _admin: object = Depends(require_admin),
):
    from flocks.security import get_secret_manager
    from flocks.tool.tool_loader import (
        delete_api_provider,
        find_api_provider_dir,
        _read_yaml_raw,
    )

    provider_dir = find_api_provider_dir(provider_id)
    provider_yaml: Dict[str, Any] = {}
    provider_path: Optional[str] = None
    config_keys = {provider_id}

    if provider_dir is not None:
        provider_path = str(provider_dir)
        config_keys.add(provider_dir.name)
        provider_file = provider_dir / "_provider.yaml"
        if provider_file.is_file():
            try:
                provider_yaml = _read_yaml_raw(provider_file)
            except Exception as e:
                log.warning("tool.draft_provider_delete.provider_read_failed", {
                    "provider_id": provider_id,
                    "error": str(e),
                })
        service_id = provider_yaml.get("service_id")
        if isinstance(service_id, str) and service_id.strip():
            config_keys.add(service_id.strip())

    raw_services = ConfigWriter.list_api_services_raw()
    config_values: List[Dict[str, Any]] = []
    for config_key in sorted(config_keys):
        raw_service = raw_services.get(config_key)
        if isinstance(raw_service, dict):
            config_values.append(raw_service)

    secret_ids = _collect_api_provider_secret_ids(provider_yaml, config_values)

    try:
        deleted_provider_path, removed_tools = delete_api_provider(provider_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        log.error("tool.draft_provider_delete.provider_delete_error", {
            "provider_id": provider_id,
            "error": str(e),
        })
        raise HTTPException(status_code=500, detail=str(e))

    removed_config_keys = [config_key for config_key in sorted(config_keys) if ConfigWriter.remove_api_service(config_key)]

    deleted_secret_ids: List[str] = []
    if delete_secrets:
        secrets = get_secret_manager()
        for secret_id in secret_ids:
            if secrets.delete(secret_id):
                deleted_secret_ids.append(secret_id)

    if deleted_provider_path is not None or removed_config_keys:
        ToolRegistry.refresh_plugin_tools()

    if deleted_provider_path is None and not removed_config_keys and not deleted_secret_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"API tool provider not found: {provider_id}")

    return DeleteAPIToolProviderResponse(
        provider_id=provider_id,
        provider_path=provider_path or (str(deleted_provider_path) if deleted_provider_path else None),
        removed_provider=deleted_provider_path is not None,
        removed_tools=removed_tools,
        removed_config_keys=removed_config_keys,
        deleted_secrets=deleted_secret_ids,
    )


@router.post(
    "",
    response_model=ToolInfoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a YAML plugin tool",
)
async def create_tool(request: CreateToolRequest, _admin: object = Depends(require_admin)):
    """
    Create a new tool via YAML plugin.

    The tool is written to ``<project>/.flocks/plugins/tools/api/`` (or a
    provider subdirectory ``api/{provider}/`` if specified), then loaded into
    the ToolRegistry immediately.
    """
    handler = dict(request.handler or {})
    if request.response and "response" not in handler:
        handler["response"] = request.response

    data: Dict[str, Any] = {
        "name": request.name,
        "description": request.description,
        "category": request.category,
        "enabled": request.enabled,
        "requires_confirmation": request.requires_confirmation,
        "handler": handler,
    }
    if request.inputSchema:
        data["inputSchema"] = request.inputSchema
    if request.parameters:
        data["parameters"] = request.parameters
    if request.provider:
        data["provider"] = request.provider

    tool, _ = await _create_and_register_yaml_tool(
        data,
        provider=request.provider,
        enabled=request.enabled,
    )
    return _build_tool_response(tool.info)


@router.put(
    "/{name}",
    response_model=ToolInfoResponse,
    summary="Update a YAML plugin tool",
)
async def update_plugin_tool(name: str, request: UpdateToolRequest, _admin: object = Depends(require_admin)):
    """
    Update an existing YAML plugin tool.

    Only YAML-based plugin tools can be updated. Built-in and MCP tools
    cannot be modified through this endpoint.
    """
    from flocks.tool.tool_loader import (
        find_yaml_tool,
        update_yaml_tool,
        yaml_to_tool,
        _read_yaml_raw,
    )

    ToolRegistry.init()

    if not find_yaml_tool(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"YAML plugin tool not found: {name}",
        )

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No updates provided",
        )

    try:
        if not update_yaml_tool(name, updates):
            raise HTTPException(status_code=500, detail=f"Failed to update YAML for tool {name}")
    except HTTPException:
        raise
    except Exception as e:
        log.error("tool.update.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))

    # Reload tool into registry
    try:
        yaml_path = find_yaml_tool(name)
        if yaml_path:
            raw = _read_yaml_raw(yaml_path)
            tool = yaml_to_tool(raw, yaml_path)
            if not tool.info.source:
                tool.info.source = "plugin_yaml"
            ToolRegistry.register(tool)
            return _build_tool_response(tool.info)
    except Exception as e:
        log.error("tool.update.reload_error", {"error": str(e), "name": name})

    existing = ToolRegistry.get(name)
    if existing:
        return _build_tool_response(existing.info)
    raise HTTPException(status_code=500, detail="Tool updated but reload failed")


@router.delete(
    "/{name}",
    summary="Delete a plugin tool",
)
async def delete_tool(name: str, _admin: object = Depends(require_admin)):
    """
    Delete a plugin tool.

    Supports YAML plugin tools and Python plugin tools. Built-in and MCP
    tools cannot be removed through this endpoint.
    """
    from flocks.tool.tool_loader import delete_yaml_tool, delete_python_tool, find_yaml_tool

    ToolRegistry.init()

    deleted = False
    if find_yaml_tool(name):
        try:
            deleted = delete_yaml_tool(name)
        except Exception as e:
            log.error("tool.delete.error", {"error": str(e), "name": name})
            raise HTTPException(status_code=500, detail=str(e))
    else:
        try:
            deleted = delete_python_tool(name)
        except Exception as e:
            log.error("tool.delete.error", {"error": str(e), "name": name})
            raise HTTPException(status_code=500, detail=str(e))

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plugin tool not found: {name}",
        )

    # Refresh plugin tools so stale decorator-registered python tools are removed too.
    ToolRegistry.refresh_plugin_tools()

    from flocks.hub import local as hub_local

    hub_local.remove_installed_record("tool", name)

    return {"status": "success", "message": f"Tool {name} deleted"}


@router.post(
    "/{name}/reload",
    response_model=ToolInfoResponse,
    summary="Reload a YAML plugin tool",
)
async def reload_tool(name: str, _admin: object = Depends(require_admin)):
    """
    Hot-reload a single YAML plugin tool.

    Re-reads the YAML file from disk and re-registers the tool
    in the ToolRegistry without restarting the service.
    """
    from flocks.tool.tool_loader import find_yaml_tool, yaml_to_tool, _read_yaml_raw

    ToolRegistry.init()

    yaml_path = find_yaml_tool(name)
    if not yaml_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"YAML plugin tool not found: {name}",
        )

    try:
        raw = _read_yaml_raw(yaml_path)
        tool = yaml_to_tool(raw, yaml_path)
        if not tool.info.source:
            tool.info.source = "plugin_yaml"
        ToolRegistry.register(tool)
        log.info("tool.reloaded", {"name": name})
        return _build_tool_response(tool.info)
    except Exception as e:
        log.error("tool.reload.error", {"error": str(e), "name": name})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/plugin/list",
    response_model=PluginToolListResponse,
    summary="List YAML plugin tools",
)
async def list_plugin_tools():
    """
    List all YAML plugin tools with metadata.

    Returns tools discovered from ``~/.flocks/plugins/tools/`` including
    provider subdirectories.
    """
    from flocks.tool.tool_loader import list_yaml_tools

    try:
        tools = list_yaml_tools()
        return PluginToolListResponse(tools=tools)
    except Exception as e:
        log.error("tool.plugin.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))
