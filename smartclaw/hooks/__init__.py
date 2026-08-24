"""
Hook system for SmartClaw

Provides an event-driven hook system for extending SmartClaw functionality.
Inspired by OpenClaw's hook architecture.
"""

from smartclaw.hooks.registry import (
    HookRegistry,
    register_hook,
    unregister_hook,
    clear_hooks,
    trigger_hook,
    get_hook_stats,
)
from smartclaw.hooks.types import (
    HookEvent,
    HookEventType,
    CommandHookEvent,
    SessionHookEvent,
    AgentHookEvent,
    SystemHookEvent,
    HookHandler,
    AsyncHookHandler,
)
from smartclaw.hooks.utils import (
    create_command_event,
    create_session_event,
    create_agent_event,
    create_system_event,
)

__all__ = [
    # Registry
    "HookRegistry",
    "register_hook",
    "unregister_hook",
    "clear_hooks",
    "trigger_hook",
    "get_hook_stats",
    
    # Types
    "HookEvent",
    "HookEventType",
    "CommandHookEvent",
    "SessionHookEvent",
    "AgentHookEvent",
    "SystemHookEvent",
    "HookHandler",
    "AsyncHookHandler",
    
    # Utils
    "create_command_event",
    "create_session_event",
    "create_agent_event",
    "create_system_event",
]
