"""Helpers for exposing per-session uploads inside sandbox containers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from flocks.workspace.manager import WorkspaceManager

UPLOADS_CHAT_PREFIX = "uploads/chat"


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
    container_workdir: str = "/workspace",
    create: bool = False,
) -> list[SandboxUploadMount]:
    """Build read-only bind mount metadata for this session's uploads."""

    upload_dir = get_session_upload_dir(session_id, create=create)
    if upload_dir is None:
        return []

    safe_id = _safe_session_id(session_id)
    if not safe_id:
        return []

    root = container_workdir.replace("\\", "/").rstrip("/") or "/workspace"
    return [
        SandboxUploadMount(
            host_dir=str(upload_dir.resolve()),
            container_dir=f"{root}/{UPLOADS_CHAT_PREFIX}/{safe_id}",
            read_only=True,
        )
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
    container_workdir: str = "/workspace",
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


def rewrite_upload_paths_for_prompt(
    text: str,
    session_id: str | None,
    *,
    container_workdir: str = "/workspace",
) -> str:
    """Replace host upload path prefixes in prompt text with sandbox paths."""

    if not text:
        return text
    mounts = get_session_upload_mounts(
        session_id,
        container_workdir=container_workdir,
    )
    rewritten = text
    for mount in mounts:
        host = str(Path(mount.host_dir))
        host_posix = Path(mount.host_dir).as_posix()
        container = mount.container_dir.rstrip("/")
        rewritten = rewritten.replace(f"file://{host_posix}", container)
        rewritten = rewritten.replace(f"file://{host}", container)
        rewritten = rewritten.replace(host_posix, container)
        rewritten = rewritten.replace(host, container)
    return rewritten.replace("\\", "/") if rewritten != text else rewritten
