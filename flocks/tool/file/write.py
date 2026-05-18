"""
Write Tool - File writing with diff generation

Writes files to the local filesystem with:
- Diff generation for existing files
- LSP diagnostics reporting
- Directory creation as needed
"""

import os
# 输出按会话隔离新增
import re
from pathlib import Path
from flocks.workspace.manager import WorkspaceManager
# --------------end----------------
from typing import Optional
from difflib import unified_diff

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.project.instance import Instance
from flocks.utils.log import Log


log = Log.create(service="tool.write")

# 输出按会话隔离新增
OUTPUT_FILE_EXTENSIONS = {
    ".csv",
    ".html",
    ".htm",
    ".json",
    ".log",
    ".md",
    ".pdf",
    ".txt",
    ".xlsx",
    ".yaml",
    ".yml",
}
# -------------------end--------------------------
DESCRIPTION = """Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- Agent-generated document outputs are routed to the Workspace outputs directory
  for the root session, regardless of sandbox mode or the requested path.
- For agent-generated files in the Workspace outputs directory, this tool will
  choose a numbered filename instead of overwriting an existing file.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""


def generate_diff(filepath: str, old_content: str, new_content: str) -> str:
    """
    Generate unified diff between old and new content
    
    Args:
        filepath: File path for diff header
        old_content: Original content
        new_content: New content
        
    Returns:
        Unified diff string
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    
    diff_lines = list(unified_diff(
        old_lines,
        new_lines,
        fromfile=filepath,
        tofile=filepath,
        lineterm=""
    ))
    
    return "".join(diff_lines)


def trim_diff(diff: str) -> str:
    """
    Trim indentation from diff content lines
    
    Ported from original trimDiff function for cleaner display.
    
    Args:
        diff: Original diff string
        
    Returns:
        Trimmed diff string
    """
    if not diff:
        return diff
    
    lines = diff.split("\n")
    
    # Find content lines (starting with +, -, or space, but not --- or +++)
    content_lines = [
        line for line in lines
        if (line.startswith("+") or line.startswith("-") or line.startswith(" "))
        and not line.startswith("---")
        and not line.startswith("+++")
    ]
    
    if not content_lines:
        return diff
    
    # Find minimum indentation
    min_indent = float('inf')
    for line in content_lines:
        content = line[1:]  # Skip the first character (+, -, or space)
        if content.strip():
            indent = len(content) - len(content.lstrip())
            min_indent = min(min_indent, indent)
    
    if min_indent == float('inf') or min_indent == 0:
        return diff
    
    # Trim lines
    trimmed_lines = []
    for line in lines:
        if (line.startswith("+") or line.startswith("-") or line.startswith(" ")) \
           and not line.startswith("---") and not line.startswith("+++"):
            prefix = line[0]
            content = line[1:]
            trimmed_lines.append(prefix + content[min_indent:])
        else:
            trimmed_lines.append(line)
    
    return "\n".join(trimmed_lines)


def _safe_relpath(path: str, start: Optional[str]) -> str:
    """Return a relative path when possible, otherwise keep the absolute path."""
    if not start:
        return path
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return path

# 输出按会话隔离新增
def _rewrite_legacy_workspace_output_path(
    filepath: str,
    session_id: Optional[str],
    source_dir: Optional[str],
) -> tuple[str, Optional[str]]:
    rewritten = WorkspaceManager.get_instance().rewrite_legacy_output_path(
        filepath,
        session_id,
        source_dir=source_dir,
    )
    if rewritten is None:
        return filepath, None
    return str(rewritten), filepath


def _safe_session_component(session_id: Optional[str]) -> str:
    if not session_id:
        return "default-session"
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", str(session_id)).strip("._-")
    return component or "default-session"


def _effective_output_session_id(ctx: ToolContext) -> Optional[str]:
    """Return the session id that should scope user-facing output files."""
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    for key in ("output_session_id", "main_session_key"):
        value = extra.get(key)
        if value:
            return str(value)
    return ctx.session_id


def _is_user_workspace_output_path(filepath: str, session_id: Optional[str]) -> bool:
    """Return True for the canonical ~/.flocks/workspace/outputs session path."""
    path = Path(filepath).expanduser()
    if not path.is_absolute():
        return False

    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()

    try:
        outputs_root = (
            WorkspaceManager.get_instance().get_user_workspace_dir() / "outputs"
        ).resolve()
        relative = resolved.relative_to(outputs_root)
    except (OSError, ValueError):
        return False

    parts = relative.parts
    if len(parts) < 3:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
        return False
    return parts[1] == _safe_session_component(session_id)


def _path_is_within(path: str | Path, root: str | Path) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
        root_resolved = Path(root).expanduser().resolve()
    except OSError:
        resolved = Path(path).expanduser().absolute()
        root_resolved = Path(root).expanduser().absolute()
    try:
        return resolved.is_relative_to(root_resolved)
    except ValueError:
        return False


def _looks_like_document_output(filepath: str) -> bool:
    return Path(filepath).suffix.lower() in OUTPUT_FILE_EXTENSIONS


def _project_plugins_root(base_dir: str) -> Path:
    return Path(base_dir).expanduser() / ".flocks" / "plugins"


def _rewrite_user_plugin_path_to_project(filepath: str, base_dir: str) -> tuple[str, Optional[str]]:
    """Route ~/.flocks/plugins writes to the project plugin directory."""
    path = Path(filepath).expanduser()
    if not path.is_absolute():
        return filepath, None

    user_plugins_root = WorkspaceManager.get_instance().get_user_workspace_dir().parent / "plugins"
    try:
        rel = path.resolve().relative_to(user_plugins_root.resolve())
    except (OSError, ValueError):
        return filepath, None

    target = _project_plugins_root(base_dir) / rel
    if _path_is_within(target, user_plugins_root):
        return filepath, None
    return str(target), filepath


def _is_flocks_plugin_path(filepath: str, base_dir: str) -> bool:
    """Return True for user/project plugin definitions that must stay loadable."""
    path = Path(filepath).expanduser()
    candidate = path if path.is_absolute() else Path(base_dir) / path

    manager = WorkspaceManager.get_instance()
    roots = [
        manager.get_user_workspace_dir().parent / "plugins",
        _project_plugins_root(base_dir),
        Path.cwd() / ".flocks" / "plugins",
    ]
    return any(_path_is_within(candidate, root) for root in roots)


def _is_sandbox_container_plugin_path(filepath: str, sandbox: Optional[dict]) -> bool:
    rel = _sandbox_container_relative_path(filepath, sandbox)
    if rel is None:
        return False
    parts = rel.split("/")
    return len(parts) >= 3 and parts[0] == ".flocks" and parts[1] == "plugins"


def _is_flocks_plugin_or_container_path(
    filepath: str,
    base_dir: str,
    sandbox: Optional[dict],
) -> bool:
    return _is_flocks_plugin_path(filepath, base_dir) or _is_sandbox_container_plugin_path(
        filepath,
        sandbox,
    )


def _is_existing_project_file(filepath: str, base_dir: str) -> bool:
    """Existing project files may be intentional source edits, not generated output."""
    path = Path(filepath)
    if not path.exists():
        return False
    return _path_is_within(path, base_dir)


def _rewrite_document_output_path(
    filepath: str,
    session_id: Optional[str],
    base_dir: str,
    sandbox: Optional[dict] = None,
) -> tuple[str, Optional[str]]:
    """Route generated document-like files to the root session outputs dir."""
    if not _looks_like_document_output(filepath):
        return filepath, None
    if _is_user_workspace_output_path(filepath, session_id):
        return filepath, None
    if _is_flocks_plugin_or_container_path(filepath, base_dir, sandbox):
        return filepath, None
    if _is_existing_project_file(filepath, base_dir):
        return filepath, None

    filename = Path(filepath).name
    if not filename:
        return filepath, None
    return str(WorkspaceManager.get_instance().get_outputs_dir(session_id) / filename), filepath


def _next_available_output_path(filepath: str) -> tuple[str, Optional[str]]:
    """Return a non-existing output path, preserving the requested path when free."""
    path = Path(filepath)
    if not path.exists():
        return filepath, None

    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return str(candidate), filepath
        index += 1


def _sandbox_container_relative_path(filepath: str, sandbox: Optional[dict]) -> Optional[str]:
    """Return path relative to the sandbox container workdir for /workspace-style paths."""
    if not isinstance(sandbox, dict):
        return None

    container_workdir = str(sandbox.get("container_workdir") or "/workspace")
    container_workdir = container_workdir.replace("\\", "/").rstrip("/") or "/workspace"
    raw = str(filepath).replace("\\", "/")
    if raw == container_workdir:
        return ""
    prefix = container_workdir + "/"
    if not raw.startswith(prefix):
        return None

    rel = raw[len(prefix):].lstrip("/")
    if not rel:
        return ""
    parts = [part for part in rel.split("/") if part]
    if any(part in (".", "..") for part in parts):
        return None
    return "/".join(parts)


def _rewrite_sandbox_container_output_path(
    filepath: str,
    session_id: Optional[str],
    sandbox: Optional[dict],
) -> tuple[str, Optional[str]]:
    """Rewrite common container-root report paths to the user-facing outputs dir."""
    rel = _sandbox_container_relative_path(filepath, sandbox)
    if not rel:
        return filepath, None

    parts = rel.split("/")
    manager = WorkspaceManager.get_instance()

    if parts[0] == "outputs" and len(parts) > 1:
        output_parts = parts[1:]
        day = None
        if output_parts and re.fullmatch(r"\d{4}-\d{2}-\d{2}", output_parts[0]):
            day = output_parts[0]
            output_parts = output_parts[1:]
            if output_parts and re.fullmatch(r"ses_[A-Za-z0-9._-]+", output_parts[0]):
                output_parts = output_parts[1:]
        if not output_parts:
            return filepath, None
        return str(manager.get_outputs_dir(session_id, day=day) / Path(*output_parts)), filepath

    if len(parts) == 1 and Path(parts[0]).suffix.lower() in OUTPUT_FILE_EXTENSIONS:
        return str(manager.get_outputs_dir(session_id) / parts[0]), filepath

    return filepath, None


def _map_sandbox_container_path_to_host(
    filepath: str,
    sandbox: Optional[dict],
) -> tuple[str, Optional[str]]:
    """Map /workspace-style container paths to the host sandbox workspace path."""
    rel = _sandbox_container_relative_path(filepath, sandbox)
    if rel is None:
        return filepath, None
    if not isinstance(sandbox, dict):
        return filepath, None
    workspace_root = sandbox.get("workspace_dir")
    if not workspace_root:
        return filepath, None
    mapped = os.path.normpath(os.path.join(str(workspace_root), *rel.split("/"))) if rel else str(workspace_root)
    return mapped, filepath
# ---------------------end--------------------------------

async def _resolve_sandbox_file_path(
    ctx: ToolContext,
    filepath: str,
) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """
    Resolve file path under sandbox workspace when sandbox is enabled.

    Returns:
        (resolved_path, error_message, sandbox_dict)
    """
    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    if not isinstance(sandbox, dict):
        return filepath, None, None

    workspace_root = sandbox.get("workspace_dir")
    if not workspace_root:
        return filepath, None, sandbox

    if not os.path.isabs(filepath):
        filepath = os.path.join(workspace_root, filepath)

    try:
        from flocks.sandbox.paths import assert_sandbox_path

        resolved = await assert_sandbox_path(
            file_path=filepath,
            cwd=workspace_root,
            root=workspace_root,
        )
        return resolved.resolved, None, sandbox
    except Exception:
        return None, (
            f"Path escapes sandbox workspace: {filepath}. "
            "Use paths inside sandbox workspace only."
        ), sandbox


@ToolRegistry.register_function(
    name="write",
    description=DESCRIPTION,
    category=ToolCategory.FILE,
    parameters=[
        ToolParameter(
            name="content",
            type=ParameterType.STRING,
            description="The content to write to the file",
            required=True
        ),
        ToolParameter(
            name="filePath",
            type=ParameterType.STRING,
            description=(
                "The absolute path to the file to write (must be absolute, not relative).\n"
                "\n"
                "IMPORTANT — choose the correct directory from <env>:\n"
                "- Project source file (source code, tests, configs that belong to the project)"
                " → Source code directory\n"
                "- Agent-generated output (scripts, reports, examples, analysis results, drafts"
                " requested by user) → Workspace outputs directory\n"
                "\n"
                "Agent-generated outputs MUST go to the Workspace outputs directory."
                " This directory is date- and session-scoped"
                " (outputs/<YYYY-MM-DD>/<session_id>/). NEVER write them"
                " into the Source code directory."
            ),
            required=True
        ),
    ]
)
async def write_tool(
    ctx: ToolContext,
    content: str,
    filePath: str,
) -> ToolResult:
    """
    Write content to a file
    
    Args:
        ctx: Tool context
        content: Content to write
        filePath: Target file path
        
    Returns:
        ToolResult with operation status
    """
    # Coerce non-string content: dicts/lists → JSON, everything else → str
    if not isinstance(content, str):
        if isinstance(content, (dict, list)):
            import json as _json
            content = _json.dumps(content, ensure_ascii=False, indent=2)
        else:
            content = str(content)
    # 输出按会话隔离修改
    # Resolve path
    filepath = filePath
    base_dir = Instance.get_directory() or os.getcwd()
    output_session_id = _effective_output_session_id(ctx)
    sandbox = ctx.extra.get("sandbox") if ctx.extra else None
    if not os.path.isabs(filepath) and _sandbox_container_relative_path(filepath, sandbox) is None:
        filepath = os.path.join(base_dir, filepath)

    filepath, rewritten_from = _rewrite_legacy_workspace_output_path(
        filepath,
        output_session_id,
        base_dir,
    )

    if rewritten_from is None:
        filepath, rewritten_from = _rewrite_user_plugin_path_to_project(filepath, base_dir)
    if rewritten_from is None:
        filepath, rewritten_from = _rewrite_sandbox_container_output_path(
            filepath,
            output_session_id,
            sandbox,
        )
    if rewritten_from is None:
        filepath, rewritten_from = _rewrite_document_output_path(
            filepath,
            output_session_id,
            base_dir,
            sandbox,
        )
    is_user_workspace_output = _is_user_workspace_output_path(filepath, output_session_id)
    is_flocks_plugin_path = _is_flocks_plugin_or_container_path(filepath, base_dir, sandbox)
    if rewritten_from is None and is_flocks_plugin_path:
        filepath, container_mapped_from = _map_sandbox_container_path_to_host(filepath, sandbox)
        if container_mapped_from is not None:
            rewritten_from = container_mapped_from
        is_flocks_plugin_path = _is_flocks_plugin_or_container_path(filepath, base_dir, sandbox)
    if rewritten_from is None and not is_user_workspace_output and not is_flocks_plugin_path:
        filepath, container_mapped_from = _map_sandbox_container_path_to_host(filepath, sandbox)
        if container_mapped_from is not None:
            rewritten_from = container_mapped_from
        filepath, sandbox_error, sandbox = await _resolve_sandbox_file_path(ctx, filepath)
        if sandbox_error:
            return ToolResult(
                success=False,
                error=sandbox_error,
                title=filePath,
            )

        filepath, post_sandbox_rewritten_from = _rewrite_legacy_workspace_output_path(
            filepath,
            output_session_id,
            base_dir,
        )
        if post_sandbox_rewritten_from is not None:
            rewritten_from = post_sandbox_rewritten_from
    #------------------------end-----------------------------------
    if isinstance(sandbox, dict) and sandbox.get("workspace_access") == "ro":
        return ToolResult(
            success=False,
            error=(
                "Write is blocked in sandbox read-only workspace mode. "
                "Set sandbox.workspace_access to 'rw' to allow writes."
            ),
            title=filePath,
        )
    # 输出按会话隔离新增
    deduplicated_from = None
    if _is_user_workspace_output_path(filepath, output_session_id):
        filepath, deduplicated_from = _next_available_output_path(filepath)
    # -----------------end-----------------------------
    # Get relative title for display
    worktree = Instance.get_worktree() or os.getcwd()
    title = _safe_relpath(filepath, worktree)
    
    # Check if file exists and get old content
    exists = os.path.exists(filepath)
    old_content = ""
    
    if exists:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                old_content = f.read()
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to read existing file: {str(e)}",
                title=title
            )
    
    # Generate diff
    diff = trim_diff(generate_diff(filepath, old_content, content))
    
    # Request permission
    await ctx.ask(
        permission="edit",
        patterns=[_safe_relpath(filepath, worktree)],
        always=["*"],
        metadata={
            "filepath": filepath,
            "diff": diff
        }
    )
    
    # Create parent directory if needed
    parent_dir = os.path.dirname(filepath)
    if parent_dir and not os.path.exists(parent_dir):
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to create directory: {str(e)}",
                title=title
            )
    
    # Write file
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Failed to write file: {str(e)}",
            title=title
        )
    
    # Build output
    output = "Wrote file successfully."
    
    # Note: LSP diagnostics integration would go here
    # For now we just return success
    
    return ToolResult(
        success=True,
        output=output,
        title=title,
        metadata={
            "filepath": filepath,
            # 输出按会话隔离新增
            "rewritten_from": rewritten_from,
            "deduplicated_from": deduplicated_from,
            #---------------end-----------------------
            "exists": exists,
            "diagnostics": {}
        }
    )
