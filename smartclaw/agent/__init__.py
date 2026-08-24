"""
Agent management module.

Canonical import locations:
  smartclaw.agent.agent         — AgentInfo, AgentModel, AgentPromptMetadata, …
  smartclaw.agent.registry      — Agent class (load / get / list)
  smartclaw.agent.agent_factory — scan_and_load, inject_dynamic_prompts
  smartclaw.agent.prompt_utils  — prompt builder functions
  smartclaw.session.prompt_strings     — PROMPT_COMPACTION/TITLE/SUMMARY/GENERATE

This __init__ exposes the public API via lazy imports to avoid circular
dependencies with session/runner.py.
"""

__all__ = [
    "Agent",
    "AgentInfo",
    "AgentModel",
    # Session management prompts
    "PROMPT_COMPACTION",
    "PROMPT_TITLE",
    "PROMPT_SUMMARY",
    # YAML config loader
    "yaml_to_agent_info",
]

_LAZY_MAP = {
    "Agent": "smartclaw.agent.registry",
    "AgentInfo": "smartclaw.agent.agent",
    "AgentModel": "smartclaw.agent.agent",
    "PROMPT_COMPACTION": "smartclaw.session.prompt_strings",
    "PROMPT_TITLE": "smartclaw.session.prompt_strings",
    "PROMPT_SUMMARY": "smartclaw.session.prompt_strings",
    "yaml_to_agent_info": "smartclaw.agent.agent_factory",
}


def __getattr__(name: str):
    if name in _LAZY_MAP:
        import importlib
        module = importlib.import_module(_LAZY_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
