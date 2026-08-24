"""
Workspace module

Manages the user-facing workspace directory (~/.smartclaw/workspace/).
Provides file management for uploads, agent outputs, and knowledge base files.
"""

from smartclaw.workspace.manager import WorkspaceManager

__all__ = ["WorkspaceManager"]
