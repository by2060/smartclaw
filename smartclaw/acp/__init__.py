"""
ACP (Agent Client Protocol) module

Provides integration with editors like Zed through the Agent Client Protocol.
Based on SmartClaw' ported src/acp/

The ACP protocol enables AI agents to communicate with IDE clients through:
- JSON-RPC over stdio
- Session management
- Tool execution notifications
- Permission requests
"""

from smartclaw.acp.types import ACPConfig, ACPSessionState
from smartclaw.acp.session import ACPSessionManager
from smartclaw.acp.agent import ACPAgent, ACP


__all__ = [
    "ACPConfig",
    "ACPSessionState",
    "ACPSessionManager",
    "ACPAgent",
    "ACP",
]
