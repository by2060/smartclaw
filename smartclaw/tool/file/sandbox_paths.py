"""Shared sandbox path mapping for file tools."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from smartclaw.tool.registry import ToolContext


@dataclass(frozen=True)
class ResolvedToolPath:
    path: str
    sandbox: Optional[dict[str, Any]] = None
    mapped_from: Optional[str] = None
    read_only: bool = False


def get_sandbox(ctx: ToolContext | None) -> Optional[dict[str, Any]]:
    extra = ctx.extra if ctx and isinstance(ctx.extra, dict) else {}
    sandbox = extra.get("sandbox")
    return sandbox if isinstance(sandbox, dict) else None


def _clean_container_root(value: object, default: str = "/workspace") -> str:
    root = str(value or default).replace("\\", "/").rstrip("/")
    return root or default


def _container_relative_path(filepath: str, sandbox: Optional[dict[str, Any]]) -> Optional[str]:
    if not isinstance(sandbox, dict):
        return None

    container_workdir = _clean_container_root(sandbox.get("container_workdir"))
    raw = str(filepath).replace("\\", "/")
    if raw.startswith("file://"):
        raw = raw[7:]
    if raw == container_workdir:
        return ""
    prefix = container_workdir + "/"
    if not raw.startswith(prefix):
        return None

    rel = raw[len(prefix):].lstrip("/")
    parts = [part for part in rel.split("/") if part]
    if any(part in (".", "..") for part in parts):
        return None
    return "/".join(parts)


def _iter_upload_mounts(sandbox: Optional[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    if not isinstance(sandbox, dict):
        return []
    mounts = sandbox.get("upload_mounts") or []
    if not isinstance(mounts, list):
        return []
    return [mount for mount in mounts if isinstance(mount, dict)]


def _path_within(path: str | Path, root: str | Path) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
        root_resolved = Path(root).expanduser().resolve()
    except OSError:
        resolved = Path(path).expanduser().absolute()
        root_resolved = Path(root).expanduser().absolute()
    try:
        resolved.relative_to(root_resolved)
        return True
    except ValueError:
        return False


async def _assert_under_root(path: str, root: str) -> str:
    from smartclaw.sandbox.paths import assert_sandbox_path

    resolved = await assert_sandbox_path(file_path=path, cwd=root, root=root)
    return resolved.resolved


def _map_container_upload_path(
    filepath: str,
    sandbox: Optional[dict[str, Any]],
) -> Optional[ResolvedToolPath]:
    raw = str(filepath).replace("\\", "/")
    if raw.startswith("file://"):
        raw = raw[7:]

    for mount in _iter_upload_mounts(sandbox):
        container_dir = _clean_container_root(mount.get("container_dir"), "")
        host_dir = mount.get("host_dir")
        if not container_dir or not host_dir:
            continue
        if raw == container_dir:
            rel = ""
        elif raw.startswith(container_dir.rstrip("/") + "/"):
            rel = raw[len(container_dir.rstrip("/") + "/"):]
        else:
            continue

        parts = [part for part in rel.split("/") if part]
        if any(part in (".", "..") for part in parts):
            return None
        mapped = Path(str(host_dir)).joinpath(*parts) if parts else Path(str(host_dir))
        return ResolvedToolPath(
            path=str(mapped),
            sandbox=sandbox,
            mapped_from=filepath,
            read_only=bool(mount.get("read_only", True)),
        )
    return None


def _map_host_upload_path(
    filepath: str,
    sandbox: Optional[dict[str, Any]],
) -> Optional[ResolvedToolPath]:
    candidate = filepath[7:] if str(filepath).startswith("file://") else filepath
    for mount in _iter_upload_mounts(sandbox):
        host_dir = mount.get("host_dir")
        if not host_dir:
            continue
        if _path_within(candidate, str(host_dir)):
            return ResolvedToolPath(
                path=candidate,
                sandbox=sandbox,
                mapped_from=None,
                read_only=bool(mount.get("read_only", True)),
            )
    return None


def _map_workspace_upload_container_path(
    filepath: str,
    sandbox: Optional[dict[str, Any]],
) -> Optional[ResolvedToolPath]:
    raw = str(filepath).replace("\\", "/")
    if raw.startswith("file://"):
        raw = raw[7:]

    container_roots = ["/workspace"]
    if isinstance(sandbox, dict):
        container_roots.insert(0, _clean_container_root(sandbox.get("container_workdir")))

    rel: str | None = None
    for root in dict.fromkeys(container_roots):
        upload_prefix = f"{root.rstrip('/')}/uploads/chat"
        if raw == upload_prefix:
            rel = "uploads/chat"
            break
        upload_task_prefix = f"{root.rstrip('/')}/uploads/task"
        if raw == upload_task_prefix:
            rel = "uploads/task"
            break
        if raw.startswith(upload_prefix + "/"):
            rel = "uploads/chat/" + raw[len(upload_prefix + "/"):]
            break
        if raw.startswith(upload_task_prefix + "/"):
            rel = "uploads/task/" + raw[len(upload_task_prefix + "/"):]
            break
    
    if rel is None:
        return None

    parts = [part for part in rel.split("/") if part]
    if any(part in (".", "..") for part in parts):
        return None

    try:
        from smartclaw.sandbox.uploads import UPLOADS_CHAT_PREFIX
        from smartclaw.workspace.manager import WorkspaceManager

        manager = WorkspaceManager.get_instance()
        mapped = manager.resolve_user_workspace_path(rel)

    except Exception:
        return None

    return ResolvedToolPath(
        path=str(mapped),
        sandbox=sandbox,
        mapped_from=filepath,
        read_only=True,
    )


def _map_workspace_output_container_path(
    ctx: ToolContext,
    filepath: str,
    sandbox: Optional[dict[str, Any]],
) -> Optional[ResolvedToolPath]:
    raw = str(filepath).replace("\\", "/")
    if raw.startswith("file://"):
        raw = raw[7:]

    container_roots = ["/workspace"]
    if isinstance(sandbox, dict):
        container_roots.insert(0, _clean_container_root(sandbox.get("container_workdir")))

    rel: str | None = None
    for root in dict.fromkeys(container_roots):
        for alias in ("outputs", "output"):
            output_prefix = f"{root.rstrip('/')}/{alias}"
            if raw == output_prefix:
                rel = "outputs"
                break
            if raw.startswith(output_prefix + "/"):
                rel = "outputs/" + raw[len(output_prefix + "/"):]
                break
        if rel is not None:
            break
        artifact_prefix = f"{root.rstrip('/')}/artifacts"
        if raw == artifact_prefix:
            rel = "outputs/artifacts"
            break
        if raw.startswith(artifact_prefix + "/"):
            rel = "outputs/artifacts/" + raw[len(artifact_prefix + "/"):]
            break
    if rel is None:
        return None

    parts = [part for part in rel.split("/") if part]
    if any(part in (".", "..") for part in parts):
        return None

    try:
        from smartclaw.workspace.manager import WorkspaceManager

        manager = WorkspaceManager.get_instance()
        session_ids = _effective_output_session_ids(ctx)
        output_root = manager.get_outputs_dir(session_ids[0] if session_ids else None)
        output_root.mkdir(parents=True, exist_ok=True)
        output_parts = parts[1:]
        if (
            len(output_parts) >= 2
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", output_parts[0])
            and output_parts[0] == output_root.parent.name
            and output_parts[1] == output_root.name
        ):
            output_parts = output_parts[2:]
        mapped = (output_root / Path(*output_parts)).resolve() if output_parts else output_root.resolve()
    except Exception:
        return None

    if not _path_within(mapped, output_root):
        return None
    return ResolvedToolPath(
        path=str(mapped),
        sandbox=sandbox,
        mapped_from=filepath,
        read_only=False,
    )


def _map_workspace_plugin_container_path(
    filepath: str,
    sandbox: Optional[dict[str, Any]],
) -> Optional[ResolvedToolPath]:
    """Map /workspace/.smartclaw/plugins to the project plugin directory."""

    if not isinstance(sandbox, dict):
        return None

    raw = str(filepath).replace("\\", "/")
    if raw.startswith("file://"):
        raw = raw[7:]

    container_roots = [
        _clean_container_root(sandbox.get("container_workdir")),
        "/workspace",
    ]

    rel: str | None = None
    for root in dict.fromkeys(container_roots):
        plugin_prefix = f"{root.rstrip('/')}/.smartclaw/plugins"
        if raw == plugin_prefix:
            rel = ".smartclaw/plugins"
            break
        if raw.startswith(plugin_prefix + "/"):
            rel = ".smartclaw/plugins/" + raw[len(plugin_prefix + "/"):]
            break
    if rel is None:
        return None

    parts = [part for part in rel.split("/") if part]
    if any(part in (".", "..") for part in parts):
        return None

    project_plugins_dir = sandbox.get("project_plugins_dir")
    if project_plugins_dir:
        project_plugins_root = Path(str(project_plugins_dir)).expanduser()
        suffix = parts[2:] if len(parts) >= 2 and parts[:2] == [".smartclaw", "plugins"] else parts
        mapped = project_plugins_root.joinpath(*suffix)
    else:
        agent_workspace_dir = sandbox.get("agent_workspace_dir")
        if not agent_workspace_dir:
            return None
        project_root = Path(str(agent_workspace_dir)).expanduser()
        project_plugins_root = project_root / ".smartclaw" / "plugins"
        mapped = project_root.joinpath(*parts)
    if not _path_within(mapped, project_plugins_root):
        return None
    return ResolvedToolPath(
        path=str(mapped),
        sandbox=sandbox,
        mapped_from=filepath,
        read_only=False,
    )


def _map_host_project_plugin_path(
    filepath: str,
    sandbox: Optional[dict[str, Any]],
) -> Optional[ResolvedToolPath]:
    """Allow host paths that are already inside the project plugin directory."""

    if not isinstance(sandbox, dict):
        return None

    project_plugins_dir = sandbox.get("project_plugins_dir")
    if not project_plugins_dir:
        return None

    candidate = filepath[7:] if str(filepath).startswith("file://") else filepath
    if not os.path.isabs(candidate):
        return None

    project_plugins_root = Path(str(project_plugins_dir)).expanduser()
    if not _path_within(candidate, project_plugins_root):
        return None

    return ResolvedToolPath(
        path=candidate,
        sandbox=sandbox,
        mapped_from=None,
        read_only=False,
    )


def _effective_output_session_ids(ctx: ToolContext) -> list[str]:
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    candidates = [
        extra.get("output_session_id"),
        extra.get("main_session_key"),
        ctx.session_id,
    ]
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        value = str(candidate)
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _map_session_output_path(
    ctx: ToolContext,
    filepath: str,
    sandbox: Optional[dict[str, Any]],
) -> Optional[ResolvedToolPath]:
    """Allow sandboxed file tools to consume current-session outputs.

    Tools such as doc_parser intentionally write generated Markdown under the
    user-facing SmartClaw outputs directory. Those files still need to be readable
    by follow-up sandbox-aware tools in the same session.
    """

    candidate = filepath[7:] if str(filepath).startswith("file://") else filepath
    if not os.path.isabs(candidate):
        return None

    try:
        from smartclaw.workspace.manager import WorkspaceManager

        manager = WorkspaceManager.get_instance()
        outputs_root = (manager.get_user_workspace_dir() / "outputs").resolve()
        resolved = Path(candidate).expanduser().resolve()
        rel = resolved.relative_to(outputs_root)
    except Exception:
        return None

    rel_parts = rel.parts
    if len(rel_parts) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rel_parts[0]):
        return None

    day = rel_parts[0]
    for session_id in _effective_output_session_ids(ctx):
        try:
            session_root = manager.get_outputs_dir(
                session_id,
                day=day,
                create=False,
            ).resolve()
        except Exception:
            continue
        if _path_within(resolved, session_root):
            return ResolvedToolPath(
                path=str(resolved),
                sandbox=sandbox,
                mapped_from=None,
                read_only=False,
            )
    return None


def is_session_output_path(ctx: ToolContext, filepath: str) -> bool:
    """Return True when a host path is inside the current session outputs tree."""

    return _map_session_output_path(ctx, filepath, get_sandbox(ctx)) is not None


def is_project_plugin_path(ctx: ToolContext, filepath: str) -> bool:
    """Return True when a path resolves inside the project plugin tree."""

    sandbox = get_sandbox(ctx)
    plugin_mapped = _map_workspace_plugin_container_path(filepath, sandbox)
    candidate = plugin_mapped.path if plugin_mapped is not None else filepath
    candidate = candidate[7:] if str(candidate).startswith("file://") else candidate

    roots: list[Path] = []
    if isinstance(sandbox, dict) and sandbox.get("project_plugins_dir"):
        roots.append(Path(str(sandbox["project_plugins_dir"])))
    if isinstance(sandbox, dict) and sandbox.get("agent_workspace_dir"):
        roots.append(Path(str(sandbox["agent_workspace_dir"])) / ".smartclaw" / "plugins")
    try:
        from smartclaw.project.instance import Instance

        if Instance.get_directory():
            roots.append(Path(str(Instance.get_directory())) / ".smartclaw" / "plugins")
    except Exception:
        pass
    roots.append(Path.cwd() / ".smartclaw" / "plugins")

    return any(_path_within(candidate, root) for root in roots)


async def resolve_sandbox_path(
    ctx: ToolContext,
    filepath: str,
    *,
    base_dir: str | None = None,
) -> tuple[Optional[ResolvedToolPath], Optional[str]]:
    """Resolve a tool path against sandbox workspace and upload mounts."""

    sandbox = get_sandbox(ctx)
    if not sandbox:
        workspace_upload = _map_workspace_upload_container_path(filepath, sandbox)
        if workspace_upload is not None:
            return workspace_upload, None
        workspace_output = _map_workspace_output_container_path(ctx, filepath, sandbox)
        if workspace_output is not None:
            return workspace_output, None
        return ResolvedToolPath(path=filepath), None

    workspace_root = sandbox.get("workspace_dir")
    if not workspace_root:
        return ResolvedToolPath(path=filepath, sandbox=sandbox), None

    upload_mapped = _map_container_upload_path(filepath, sandbox)
    if upload_mapped is not None:
        try:
            mount_root = next(
                str(mount.get("host_dir"))
                for mount in _iter_upload_mounts(sandbox)
                if mount.get("host_dir") and _path_within(upload_mapped.path, str(mount.get("host_dir")))
            )
            resolved = await _assert_under_root(upload_mapped.path, mount_root)
        except Exception:
            return None, (
                f"Path escapes sandbox upload mount: {filepath}. "
                "Use paths inside the current session upload directory only."
            )
        return ResolvedToolPath(
            path=resolved,
            sandbox=sandbox,
            mapped_from=upload_mapped.mapped_from,
            read_only=upload_mapped.read_only,
        ), None

    host_upload = _map_host_upload_path(filepath, sandbox)
    if host_upload is not None:
        for mount in _iter_upload_mounts(sandbox):
            host_dir = mount.get("host_dir")
            if host_dir and _path_within(host_upload.path, str(host_dir)):
                try:
                    resolved = await _assert_under_root(host_upload.path, str(host_dir))
                    return ResolvedToolPath(
                        path=resolved,
                        sandbox=sandbox,
                        read_only=host_upload.read_only,
                    ), None
                except Exception:
                    break
        return None, (
            f"Path escapes sandbox upload mount: {filepath}. "
            "Use paths inside the current session upload directory only."
        )

    workspace_upload = _map_workspace_upload_container_path(filepath, sandbox)
    if workspace_upload is not None:
        try:
            from smartclaw.sandbox.uploads import UPLOADS_CHAT_PREFIX
            from smartclaw.workspace.manager import WorkspaceManager

            uploads_root = WorkspaceManager.get_instance().resolve_user_workspace_path(UPLOADS_CHAT_PREFIX)
            resolved = await _assert_under_root(workspace_upload.path, str(uploads_root))
        except Exception:
            return None, (
                f"Path escapes sandbox upload mount: {filepath}. "
                "Use paths inside the current session upload directory only."
            )
        return ResolvedToolPath(
            path=resolved,
            sandbox=sandbox,
            mapped_from=workspace_upload.mapped_from,
            read_only=True,
        ), None

    workspace_output = _map_workspace_output_container_path(ctx, filepath, sandbox)
    if workspace_output is not None:
        try:
            from smartclaw.workspace.manager import WorkspaceManager

            manager = WorkspaceManager.get_instance()
            session_ids = _effective_output_session_ids(ctx)
            outputs_root = manager.get_outputs_dir(session_ids[0] if session_ids else None)
            resolved = await _assert_under_root(workspace_output.path, str(outputs_root))
        except Exception:
            return None, (
                f"Path escapes sandbox outputs directory: {filepath}. "
                "Use paths inside <workspace>/outputs only."
            )
        return ResolvedToolPath(
            path=resolved,
            sandbox=sandbox,
            mapped_from=workspace_output.mapped_from,
            read_only=False,
        ), None

    workspace_plugin = _map_workspace_plugin_container_path(filepath, sandbox)
    if workspace_plugin is not None:
        try:
            project_plugins_dir = sandbox.get("project_plugins_dir")
            if project_plugins_dir:
                plugins_root = Path(str(project_plugins_dir)).expanduser()
            else:
                agent_workspace_dir = sandbox.get("agent_workspace_dir")
                if not agent_workspace_dir:
                    raise ValueError("missing agent workspace")
                plugins_root = Path(str(agent_workspace_dir)).expanduser() / ".smartclaw" / "plugins"
            resolved = await _assert_under_root(workspace_plugin.path, str(plugins_root))
        except Exception:
            return None, (
                f"Path escapes project plugin directory: {filepath}. "
                "Use paths inside <workspace>/.smartclaw/plugins only."
            )
        return ResolvedToolPath(
            path=resolved,
            sandbox=sandbox,
            mapped_from=workspace_plugin.mapped_from,
            read_only=False,
        ), None

    host_project_plugin = _map_host_project_plugin_path(filepath, sandbox)
    if host_project_plugin is not None:
        try:
            plugins_root = Path(str(sandbox["project_plugins_dir"])).expanduser()
            resolved = await _assert_under_root(host_project_plugin.path, str(plugins_root))
        except Exception:
            return None, (
                f"Path escapes project plugin directory: {filepath}. "
                "Use paths inside <workspace>/.smartclaw/plugins only."
            )
        return ResolvedToolPath(
            path=resolved,
            sandbox=sandbox,
            mapped_from=host_project_plugin.mapped_from,
            read_only=host_project_plugin.read_only,
        ), None


    output_mapped = _map_session_output_path(ctx, filepath, sandbox)
    if output_mapped is not None:
        return output_mapped, None

    rel = _container_relative_path(filepath, sandbox)
    if rel is not None:
        candidate = os.path.normpath(os.path.join(str(workspace_root), *rel.split("/"))) if rel else str(workspace_root)
        mapped_from = filepath
    else:
        candidate = filepath[7:] if str(filepath).startswith("file://") else filepath
        if not os.path.isabs(candidate):
            candidate = os.path.join(str(workspace_root), candidate)
        mapped_from = None

    try:
        resolved = await _assert_under_root(candidate, str(workspace_root))
        return ResolvedToolPath(
            path=resolved,
            sandbox=sandbox,
            mapped_from=mapped_from,
            read_only=False,
        ), None
    except Exception:
        return None, (
            f"Path escapes sandbox workspace: {filepath}. "
            "Use paths inside sandbox workspace or current session uploads only."
        )


def is_upload_read_only(resolved: ResolvedToolPath | None) -> bool:
    return bool(resolved and resolved.read_only)


def _display_workspace_upload_path(path: str) -> Optional[str]:
    try:
        from smartclaw.sandbox.uploads import UPLOADS_CHAT_PREFIX
        from smartclaw.workspace.manager import WorkspaceManager

        manager = WorkspaceManager.get_instance()
        uploads_root = manager.resolve_user_workspace_path(UPLOADS_CHAT_PREFIX).resolve()
        resolved = Path(path).expanduser().resolve()
        rel = resolved.relative_to(uploads_root)
        suffix = rel.as_posix()
        return f"/workspace/{UPLOADS_CHAT_PREFIX}/{suffix}" if suffix else f"/workspace/{UPLOADS_CHAT_PREFIX}"
    except Exception:
        return None


def _display_workspace_output_path(path: str) -> Optional[str]:
    try:
        from smartclaw.workspace.manager import WorkspaceManager

        manager = WorkspaceManager.get_instance()
        outputs_root = (manager.get_user_workspace_dir() / "outputs").resolve()
        resolved = Path(path).expanduser().resolve()
        rel = resolved.relative_to(outputs_root)
        suffix = rel.as_posix()
        return f"/workspace/outputs/{suffix}" if suffix else "/workspace/outputs"
    except Exception:
        return None


def sandbox_search_roots(
    ctx: ToolContext,
    default_root: str,
    requested_path: str | None = None,
) -> list[str]:
    """Return host search roots for a sandbox-aware search tool."""

    sandbox = get_sandbox(ctx)
    if not sandbox:
        return [requested_path or default_root]
    if requested_path:
        return [requested_path]

    roots = [str(sandbox.get("workspace_dir") or default_root)]
    for mount in _iter_upload_mounts(sandbox):
        host_dir = mount.get("host_dir")
        if host_dir and os.path.isdir(str(host_dir)):
            roots.append(str(host_dir))
    return roots


def display_path(path: str, ctx: ToolContext | None) -> str:
    """Return a sandbox-visible path for tool output when possible."""

    sandbox = get_sandbox(ctx)
    if upload_display := _display_workspace_upload_path(path):
        return upload_display
    if output_display := _display_workspace_output_path(path):
        return output_display
    if not sandbox:
        return path

    for mount in _iter_upload_mounts(sandbox):
        host_dir = mount.get("host_dir")
        container_dir = mount.get("container_dir")
        if not host_dir or not container_dir or not _path_within(path, str(host_dir)):
            continue
        try:
            rel = Path(path).expanduser().resolve().relative_to(Path(str(host_dir)).expanduser().resolve())
        except (OSError, ValueError):
            continue
        suffix = rel.as_posix()
        return f"{str(container_dir).rstrip('/')}/{suffix}" if suffix else str(container_dir)

    workspace_root = sandbox.get("workspace_dir")
    container_workdir = _clean_container_root(sandbox.get("container_workdir"))
    if workspace_root and _path_within(path, str(workspace_root)):
        try:
            rel = Path(path).expanduser().resolve().relative_to(Path(str(workspace_root)).expanduser().resolve())
        except (OSError, ValueError):
            return path
        suffix = rel.as_posix()
        return f"{container_workdir}/{suffix}" if suffix else container_workdir
    return path
