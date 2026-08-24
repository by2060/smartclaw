"""
smartclaw_mcp — MCP server management tool for Titan.

Supports: list, add, remove, connect, disconnect.
"""

import asyncio
import json
from typing import Any, Dict, Optional

from smartclaw.mcp.utils import (
    extract_api_key_from_mcp_url,
    get_connect_block_reason,
    normalize_mcp_config,
    should_allow_unconnected_add,
    should_skip_connect_on_add,
)
from smartclaw.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from smartclaw.utils.log import Log

log = Log.create(service="tool.smartclaw_mcp")


@ToolRegistry.register_function(
    name="smartclaw_mcp",
    description=(
        "Manage MCP servers registered in SmartClaw. "
        "Use 'list' to see all servers and their status. "
        "Use 'add' to register and connect a new MCP server (persists to smartclaw.json and ~/.smartclaw/plugins/tools/mcp/). "
        "Use 'remove' to delete a server from config and disconnect it. "
        "Use 'connect' / 'disconnect' to control an already-configured server's connection."
    ),
    description_cn=(
        "管理 SmartClaw 中注册的 MCP 服务。使用 list 查看所有服务及状态；"
        "使用 add 注册并连接新的 MCP 服务；使用 remove 从配置中删除并断开；"
        "使用 connect / disconnect 控制已配置服务的连接状态。"
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="subcommand",
            type=ParameterType.STRING,
            description="Action to perform: list | add | remove | connect | disconnect",
            required=True,
        ),
        ToolParameter(
            name="name",
            type=ParameterType.STRING,
            description=(
                "MCP server name in kebab-case (e.g. 'brave-search'). "
                "Required for add, remove, connect, disconnect."
            ),
            required=False,
        ),
        ToolParameter(
            name="config",
            type=ParameterType.OBJECT,
            description=(
                "Server configuration dict. Required for 'add'. "
                "Local example: {\"type\": \"local\", \"command\": [\"python\", \"-m\", \"pkg\"], \"enabled\": true}. "
                "Remote example: {\"type\": \"remote\", \"url\": \"https://...\", \"enabled\": true}. "
                "Use {secret:key_name} for sensitive values in environment/headers."
            ),
            required=False,
        ),
    ],
)
async def smartclaw_mcp(
    ctx: ToolContext,
    subcommand: str,
    name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> ToolResult:
    subcommand = subcommand.strip().lower()

    if subcommand == "list":
        return await _list()
    elif subcommand == "add":
        if not name:
            return ToolResult(success=False, error="'add' requires a server name.")
        if not config:
            return ToolResult(success=False, error="'add' requires a config dict.")
        return await _add(name, config)
    elif subcommand == "remove":
        if not name:
            return ToolResult(success=False, error="'remove' requires a server name.")
        return await _remove(name)
    elif subcommand == "connect":
        if not name:
            return ToolResult(success=False, error="'connect' requires a server name.")
        return await _connect(name)
    elif subcommand == "disconnect":
        if not name:
            return ToolResult(success=False, error="'disconnect' requires a server name.")
        return await _disconnect(name)
    else:
        return ToolResult(
            success=False,
            error=f"Unknown subcommand '{subcommand}'. Valid: list | add | remove | connect | disconnect",
        )


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------


async def _list() -> ToolResult:
    from smartclaw.config.config_writer import ConfigWriter
    from smartclaw.mcp import MCP

    status = await MCP.status()
    result: Dict[str, Any] = {}

    for sname, info in status.items():
        result[sname] = {
            "status": info.status.value if hasattr(info.status, "value") else str(info.status),
            "tools_count": getattr(info, "tools_count", 0),
            "error": getattr(info, "error", None),
            "connected_at": str(info.connected_at) if getattr(info, "connected_at", None) else None,
        }

    # Include servers in config but not yet in memory
    configured = ConfigWriter.list_mcp_servers()
    for sname, scfg in configured.items():
        if sname not in result:
            enabled = scfg.get("enabled", True) if isinstance(scfg, dict) else True
            if enabled:
                result[sname] = {"status": "disconnected", "tools_count": 0, "error": None, "connected_at": None}

    if not result:
        return ToolResult(success=True, output="No MCP servers configured.")

    lines = [f"{'Server':<30} {'Status':<15} {'Tools':>5}", "-" * 55]
    for sname, info in sorted(result.items()):
        err = f"  ({info['error']})" if info.get("error") else ""
        lines.append(f"{sname:<30} {info['status']:<15} {info['tools_count']:>5}{err}")

    return ToolResult(success=True, output="\n".join(lines))


async def _add(name: str, config: Dict[str, Any]) -> ToolResult:
    from smartclaw.config.config_writer import ConfigWriter
    from smartclaw.mcp import MCP
    from smartclaw.tool.tool_loader import save_mcp_config

    # config may arrive as JSON string when passed through LLM tool call
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"config is not valid JSON: {e}")

    config = extract_api_key_from_mcp_url(name, normalize_mcp_config(config))

    if should_skip_connect_on_add(config):
        ConfigWriter.add_mcp_server(name, config)
        save_mcp_config(name, config)
        return ToolResult(
            success=True,
            output={
                "message": (
                    f"MCP server '{name}' added successfully, but it was not connected yet. "
                    "Configure credentials if needed and run connect later."
                ),
                "connected": False,
                "pending_credentials": True,
                "persisted_to": ["smartclaw.json", f"~/.smartclaw/plugins/tools/mcp/{name.replace('-', '_')}.yaml"],
            },
        )

    # Attempt connection
    try:
        success = await MCP.connect(name, config)
    except Exception as e:
        return ToolResult(success=False, error=f"Connection failed: {e}")

    if not success:
        status = await MCP.status()
        info = status.get(name)
        err = getattr(info, "error", None) if info else None
        if should_allow_unconnected_add(config, err):
            await MCP.remove(name)
            ConfigWriter.add_mcp_server(name, config)
            save_mcp_config(name, config)
            log.info("smartclaw_mcp.add.deferred", {"name": name, "reason": err or "auth_pending"})
            return ToolResult(
                success=True,
                output={
                    "message": (
                        f"MCP server '{name}' added successfully, but it was not connected yet. "
                        "Configure credentials and run connect later."
                    ),
                    "connected": False,
                    "pending_credentials": True,
                    "persisted_to": ["smartclaw.json", f"~/.smartclaw/plugins/tools/mcp/{name.replace('-', '_')}.yaml"],
                    "error": err,
                },
            )
        return ToolResult(
            success=False,
            error=f"Failed to connect to '{name}'. {err or 'Check the config and server availability.'}",
        )

    # Persist to smartclaw.json
    ConfigWriter.add_mcp_server(name, config)

    # Persist YAML description to ~/.smartclaw/plugins/tools/mcp/
    save_mcp_config(name, config)

    # Get tool count
    status = await MCP.status()
    info = status.get(name)
    tools_count = getattr(info, "tools_count", 0) if info else 0

    log.info("smartclaw_mcp.add.success", {"name": name, "tools_count": tools_count})
    return ToolResult(
        success=True,
        output={
            "message": f"MCP server '{name}' added and connected successfully.",
            "tools_count": tools_count,
            "persisted_to": ["smartclaw.json", f"~/.smartclaw/plugins/tools/mcp/{name.replace('-', '_')}.yaml"],
        },
    )


async def _remove(name: str) -> ToolResult:
    from smartclaw.config.config_writer import ConfigWriter
    from smartclaw.mcp import MCP
    from smartclaw.tool.tool_loader import delete_mcp_config

    removed_config = ConfigWriter.remove_mcp_server(name)

    status = await MCP.status()
    in_memory = name in status
    if in_memory:
        await MCP.remove(name)

    delete_mcp_config(name)

    if not removed_config and not in_memory:
        return ToolResult(success=False, error=f"MCP server '{name}' not found in config or memory.")

    log.info("smartclaw_mcp.remove.success", {"name": name})
    return ToolResult(success=True, output=f"MCP server '{name}' removed successfully.")


async def _connect(name: str) -> ToolResult:
    from smartclaw.config.config import Config
    from smartclaw.config.config_writer import ConfigWriter
    from smartclaw.mcp import MCP

    try:
        config = await Config.get()
        mcp_config = getattr(config, "mcp", None) or {}
        server_config = mcp_config.get(name) if isinstance(mcp_config, dict) else None
    except Exception as e:
        return ToolResult(success=False, error=f"Failed to load MCP config: {e}")

    if server_config is None:
        server_config = ConfigWriter.get_mcp_server(name)

    if not server_config:
        return ToolResult(
            success=False,
            error=f"Server '{name}' not found in smartclaw.json. Use 'add' to register it first.",
        )

    if hasattr(server_config, "model_dump"):
        server_config = server_config.model_dump()
    elif not isinstance(server_config, dict):
        server_config = dict(server_config)
    server_config = normalize_mcp_config(server_config)

    blocked_reason = get_connect_block_reason(server_config)
    if blocked_reason:
        return ToolResult(success=False, error=blocked_reason)

    timeout_seconds = max(float(server_config.get("timeout", 30.0) or 30.0), 1.0) + 2.0
    try:
        success = await asyncio.wait_for(
            MCP.connect(name, server_config),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return ToolResult(
            success=False,
            error=f"Connection timed out while connecting to '{name}'.",
        )
    except Exception as e:
        return ToolResult(success=False, error=f"Connection failed: {e}")

    if not success:
        status = await MCP.status()
        info = status.get(name)
        err = getattr(info, "error", None) if info else None
        return ToolResult(success=False, error=f"Failed to connect to '{name}'. {err or ''}")

    status = await MCP.status()
    info = status.get(name)
    tools_count = getattr(info, "tools_count", 0) if info else 0

    return ToolResult(
        success=True,
        output=f"MCP server '{name}' connected. {tools_count} tools available.",
    )


async def _disconnect(name: str) -> ToolResult:
    from smartclaw.mcp import MCP

    status = await MCP.status()
    if name not in status:
        return ToolResult(success=False, error=f"MCP server '{name}' is not in memory (already disconnected or not found).")

    try:
        success = await MCP.disconnect(name)
    except Exception as e:
        return ToolResult(success=False, error=f"Disconnect failed: {e}")

    if not success:
        return ToolResult(success=False, error=f"Failed to disconnect from '{name}'.")

    return ToolResult(success=True, output=f"MCP server '{name}' disconnected.")
