"""
沙箱默认常量定义。
"""

import os
from pathlib import Path


def _get_state_dir() -> str:
    """获取 smartclaw state 目录."""
    root = os.getenv("SMARTCLAW_ROOT", str(Path.home() / ".smartclaw"))
    return os.path.join(root, "data")


STATE_DIR = _get_state_dir()

# 工作区
DEFAULT_SANDBOX_WORKSPACE_ROOT = os.path.join(STATE_DIR, "sandboxes")

# Docker
DEFAULT_SANDBOX_IMAGE = "python:slim"
DEFAULT_SANDBOX_CONTAINER_PREFIX = "smartclaw-sbx-"
DEFAULT_SANDBOX_WORKDIR = os.getcwd()

# 生命周期
DEFAULT_SANDBOX_IDLE_HOURS = 24
DEFAULT_SANDBOX_MAX_AGE_DAYS = 7

# Agent workspace 容器内挂载点
SANDBOX_AGENT_WORKSPACE_MOUNT = "/agent"

# 注册表
SANDBOX_STATE_DIR = os.path.join(STATE_DIR, "sandbox")
SANDBOX_REGISTRY_PATH = os.path.join(SANDBOX_STATE_DIR, "containers.json")
