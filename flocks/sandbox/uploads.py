"""Helpers for exposing per-session uploads inside sandbox containers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from flocks.workspace.manager import WorkspaceManager

UPLOADS_CHAT_PREFIX = "uploads/chat"
UPLOADS_TASK_PREFIX = "uploads/task"


class SandboxUploadMount(BaseModel):
    """Read-only upload directory mount metadata."""

    host_dir: str = Field(description="Host upload directory")
    container_dir: str = Field(description="Container-visible upload directory")
    read_only: bool = True


def _safe_session_id(session_id: str | None) -> Optional[str]:
    raw = (session_id or "").strip()
    if not raw or "/" in raw or "\\" in raw or raw in {".", ".."}:
        return None
    return raw


def get_session_upload_dir(session_id: str | None, *, create: bool = False) -> Optional[Path]:
    """Return the host upload directory for a session if it exists."""

    safe_id = _safe_session_id(session_id)
    if not safe_id:
        return None

    ws = WorkspaceManager.get_instance()
    try:
        upload_dir = ws.resolve_user_workspace_path(f"{UPLOADS_CHAT_PREFIX}/{safe_id}")
    except ValueError:
        return None
    if create:
        upload_dir.mkdir(parents=True, exist_ok=True)
    if not upload_dir.exists() or not upload_dir.is_dir():
        return None
    return upload_dir


def get_session_upload_mounts(
    session_id: str | None,
    *,
    container_workdir: str = os.getcwd(),
    create: bool = False,
) -> list[SandboxUploadMount]:
    """Build read-only bind mount metadata for this session's uploads."""

    upload_dir = get_session_upload_dir(session_id, create=create)
    if upload_dir is None:
        return []

    safe_id = _safe_session_id(session_id)
    if not safe_id:
        return []

    # 增加task目录，实现有点丑
    return [
        SandboxUploadMount(
            host_dir=str(upload_dir.resolve()),
            container_dir=str(upload_dir.resolve()),
            read_only=True,
        ),
        SandboxUploadMount(
            host_dir=str(upload_dir.resolve()).replace(UPLOADS_CHAT_PREFIX, UPLOADS_TASK_PREFIX),
            container_dir=str(upload_dir.resolve()).replace(UPLOADS_CHAT_PREFIX, UPLOADS_TASK_PREFIX),
            read_only=True,
        ),
    ]


def upload_mount_binds(mounts: list[SandboxUploadMount]) -> list[str]:
    """Return Docker -v bind strings for upload mounts."""

    return [
        f"{mount.host_dir}:{mount.container_dir}:ro"
        for mount in mounts
        if mount.host_dir and mount.container_dir
    ]


def container_path_for_upload(
    host_path: str | Path,
    session_id: str | None,
    *,
    container_workdir: str = os.getcwd(),
) -> Optional[str]:
    """Map a host upload path to its sandbox-visible container path."""

    mounts = get_session_upload_mounts(
        session_id,
        container_workdir=container_workdir,
    )
    if not mounts:
        return None

    try:
        resolved = Path(host_path).expanduser().resolve()
    except OSError:
        resolved = Path(host_path).expanduser().absolute()

    for mount in mounts:
        host_root = Path(mount.host_dir).resolve()
        try:
            rel = resolved.relative_to(host_root)
        except ValueError:
            continue
        suffix = rel.as_posix()
        return f"{mount.container_dir.rstrip('/')}/{suffix}" if suffix else mount.container_dir
    return None

