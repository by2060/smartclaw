"""
Path routes for SmartClaw TUI compatibility

Provides /path endpoint that SmartClaw SDK expects.

SmartClaw expects:
{
    "home": string,
    "state": string,
    "config": string,
    "worktree": string,
    "directory": string
}
"""

import os
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from smartclaw.utils.log import Log
from smartclaw.config.config import Config


router = APIRouter()
log = Log.create(service="path-routes")


class PathResponse(BaseModel):
    """
    Path information response - SmartClaw TUI compatible format.
    
    SmartClaw expects:
    {
        "home": string,      // User home directory
        "state": string,     // State/data directory (~/.local/share/smartclaw or XDG)
        "config": string,    // Config directory (~/.config/smartclaw or XDG)
        "worktree": string,  // Git worktree root (same as directory for now)
        "directory": string  // Current project directory
    }
    """
    home: str
    state: str
    config: str
    worktree: str
    directory: str


def get_state_dir() -> str:
    """Get state/data directory following XDG spec"""
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return os.path.join(xdg_data, "smartclaw")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "smartclaw")


def get_config_dir() -> str:
    """Get config directory following XDG spec"""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return os.path.join(xdg_config, "smartclaw")
    # Check for legacy ~/.smartclaw directory
    legacy_dir = os.path.join(os.path.expanduser("~"), ".smartclaw")
    if os.path.exists(legacy_dir):
        return legacy_dir
    return os.path.join(os.path.expanduser("~"), ".config", "smartclaw")


def get_worktree(directory: str) -> str:
    """
    Get git worktree root for directory.
    
    For now, returns the directory itself.
    TODO: Implement actual git worktree detection.
    """
    # Try to find .git directory by walking up
    current = Path(directory).resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return str(current)
        current = current.parent
    # Not in a git repo, return original directory
    return directory


@router.get(
    "",
    response_model=PathResponse,
    summary="Get paths",
    description="Retrieve path information for SmartClaw TUI"
)
async def get_paths(
    directory: Optional[str] = Query(None, description="Project directory"),
) -> PathResponse:
    """Get path information"""
    project_dir = directory or os.getcwd()
    
    return PathResponse(
        home=os.path.expanduser("~"),
        state=get_state_dir(),
        config=get_config_dir(),
        worktree=get_worktree(project_dir),
        directory=project_dir,
    )
