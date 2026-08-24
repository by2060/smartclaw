"""
Unified plugin system for SmartClaw.

Subsystems register extension points; ``PluginLoader`` scans
``~/.smartclaw/plugins/{subdir}/`` and dispatches items to each consumer.

Default directory layout::

    ~/.smartclaw/plugins/
    ├── agents/    # AGENTS: List[AgentInfo]
    ├── tools/     # TOOLS:  List[dict]  or  @ToolRegistry.register_function
    └── hooks/     # HOOKS:  Dict[str, Callable]
"""

from smartclaw.plugin.loader import (
    DEFAULT_PLUGIN_ROOT,
    ExtensionPoint,
    PluginLoader,
    load_module,
    scan_directory,
)

__all__ = [
    "DEFAULT_PLUGIN_ROOT",
    "ExtensionPoint",
    "PluginLoader",
    "load_module",
    "scan_directory",
]
