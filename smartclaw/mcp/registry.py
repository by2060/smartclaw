"""
MCP Tool Registry

Tracks MCP tool metadata and manages tool registration/deregistration
"""

import time
from typing import Dict, List, Optional
from smartclaw.mcp.types import McpToolSource
from smartclaw.utils.log import Log

log = Log.create(service="mcp.registry")


class McpToolRegistry:
    """
    MCP Tool Registry
    
    Tracks MCP tool metadata:
    - Tool source (which MCP server)
    - Mapping between original tool name and SmartClaw tool name
    - Registration time
    - Schema hash (for detecting changes)
    """
    
    # Tool metadata: SmartClaw tool name -> McpToolSource
    _tools: Dict[str, McpToolSource] = {}
    
    # Server -> tool list mapping
    _server_tools: Dict[str, List[str]] = {}
    
    @classmethod
    def track(
        cls,
        server_name: str,
        mcp_tool_name: str,
        smartclaw_tool_name: str,
        schema_hash: Optional[str] = None
    ) -> None:
        """
        Track MCP tool registration
        
        Args:
            server_name: MCP server name
            mcp_tool_name: Original MCP tool name
            smartclaw_tool_name: Tool name registered in SmartClaw
            schema_hash: Schema hash value (optional)
        """
        # Record tool metadata
        cls._tools[smartclaw_tool_name] = McpToolSource(
            mcp_server=server_name,
            mcp_tool=mcp_tool_name,
            smartclaw_tool=smartclaw_tool_name,
            registered_at=time.time(),
            schema_hash=schema_hash
        )
        
        # Record server tool list
        if server_name not in cls._server_tools:
            cls._server_tools[server_name] = []
        cls._server_tools[server_name].append(smartclaw_tool_name)
        
        log.debug("mcp.registry.tracked", {
            "server": server_name,
            "mcp_tool": mcp_tool_name,
            "smartclaw_tool": smartclaw_tool_name
        })
    
    @classmethod
    def untrack(cls, smartclaw_tool_name: str) -> None:
        """
        Untrack a tool
        
        Args:
            smartclaw_tool_name: SmartClaw tool name
        """
        if smartclaw_tool_name in cls._tools:
            source = cls._tools.pop(smartclaw_tool_name)
            
            # Remove from server tool list
            if source.mcp_server in cls._server_tools:
                try:
                    cls._server_tools[source.mcp_server].remove(smartclaw_tool_name)
                except ValueError:
                    pass
            
            log.debug("mcp.registry.untracked", {
                "smartclaw_tool": smartclaw_tool_name
            })
    
    @classmethod
    def untrack_server(cls, server_name: str) -> List[str]:
        """
        Untrack all tools from a server
        
        Args:
            server_name: Server name
            
        Returns:
            List of untracked tool names
        """
        tool_names = cls._server_tools.get(server_name, []).copy()
        
        for tool_name in tool_names:
            cls._tools.pop(tool_name, None)
        
        cls._server_tools.pop(server_name, None)
        
        log.info("mcp.registry.server_untracked", {
            "server": server_name,
            "tools_count": len(tool_names)
        })
        
        return tool_names
    
    @classmethod
    def get_source(cls, smartclaw_tool_name: str) -> Optional[McpToolSource]:
        """
        Get tool source information
        
        Args:
            smartclaw_tool_name: SmartClaw tool name
            
        Returns:
            Tool source information, or None if not found
        """
        return cls._tools.get(smartclaw_tool_name)
    
    @classmethod
    def get_server_tools(cls, server_name: str) -> List[str]:
        """
        Get all tools from a server
        
        Args:
            server_name: Server name
            
        Returns:
            List of tool names
        """
        return cls._server_tools.get(server_name, []).copy()
    
    @classmethod
    def is_mcp_tool(cls, smartclaw_tool_name: str) -> bool:
        """
        Check if a tool is an MCP tool
        
        Args:
            smartclaw_tool_name: SmartClaw tool name
            
        Returns:
            True if it's an MCP tool
        """
        return smartclaw_tool_name in cls._tools
    
    @classmethod
    def get_all_servers(cls) -> List[str]:
        """
        Get all servers that have registered tools
        
        Returns:
            List of server names
        """
        return list(cls._server_tools.keys())
    
    @classmethod
    def get_stats(cls) -> Dict[str, int]:
        """
        Get statistics
        
        Returns:
            Statistics dictionary
        """
        return {
            "total_tools": len(cls._tools),
            "total_servers": len(cls._server_tools),
            "tools_by_server": {
                server: len(tools)
                for server, tools in cls._server_tools.items()
            }
        }
    
    @classmethod
    def clear(cls) -> None:
        """Clear all tracking information (for testing)"""
        cls._tools.clear()
        cls._server_tools.clear()
        log.debug("mcp.registry.cleared")


__all__ = ['McpToolRegistry']
