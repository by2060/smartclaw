"""
Bash Tool - Shell command execution

Executes bash commands with:
- Configurable timeout
- Working directory support
- Output streaming
- Permission system integration
- Sandbox support (Docker container isolation, aligned with OpenClaw)
"""

import os
import sys
import asyncio
import subprocess
import shlex
import tempfile
import re
import shutil
import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from flocks.sandbox.types import BashSandboxConfig

from flocks.tool.registry import ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
from flocks.project.instance import Instance
from flocks.utils.log import Log
from flocks.tool.code.shell_risk import classify_shell_risk, high_risk_block_message


log = Log.create(service="tool.bash")


# Constants
MAX_METADATA_LENGTH = 30_000
DEFAULT_TIMEOUT_MS = 2 * 60 * 1000  # 2 minutes
MAX_OUTPUT_LINES = 1000
MAX_OUTPUT_BYTES = 100 * 1024  # 100KB
DEFAULT_PATH = os.environ.get(
    "PATH",
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
)
# 输出按会话隔离新增，解决绕过file/write工具写文件时文件输出目录不正确问题
OUTPUT_FILE_EXTENSIONS = (
    "csv",
    "html",
    "htm",
    "json",
    "log",
    "md",
    "pdf",
    "txt",
    "xlsx",
    "yaml",
    "yml",
)
SCRIPT_FILE_EXTENSIONS = (
    "bash",
    "js",
    "ps1",
    "py",
    "sh",
    "ts",
)
# ---------------------end-------------------


def get_description(directory: str) -> str:
    """Get tool description with directory placeholder replaced"""
    return f"""Executes a given bash command in a persistent shell session with optional timeout, ensuring proper handling and security measures.

All commands run in {directory} by default. Use the `workdir` parameter if you need to run a command in a different directory. AVOID using `cd <directory> && <command>` patterns - use `workdir` instead.

IMPORTANT: This tool is for terminal operations like git, npm, docker, etc. DO NOT use it for file operations (reading, writing, editing, searching, finding files) - use the specialized tools for this instead.
High-risk operations such as stopping services, closing or blocking ports, killing processes, changing firewall/security group/network policy rules, or restarting services/hosts/containers must not be executed directly when they relate to operational clues from logs, configs, deployment output, or earlier turns. Stop, explain the impact scope, and require explicit user authorization; if permissions or a dedicated operations agent are missing, treat it as a capability gap.
Generated reports, summaries, analysis documents, tables, JSON/CSV exports, and other user-facing output files MUST be written with the Write tool so they are saved under the Workspace outputs directory for the root session. Do not use Bash redirection, tee, Python one-liners, or shell scripts to create those files.
When Python is executed via Bash, FLOCKS_OUTPUTS_DIR already points to the final session output directory. Use it directly and do not append YYYY-MM-DD or session_id again.

Before executing the command, please follow these steps:

1. Directory Verification:
   - If the command will create new directories or files, first use `ls` to verify the parent directory exists and is the correct location
   - For example, before running "mkdir foo/bar", first use `ls foo` to check that "foo" exists and is the intended parent directory

2. Command Execution:
   - Always quote file paths that contain spaces with double quotes (e.g., rm "path with spaces/file.txt")
   - Examples of proper quoting:
     - mkdir "/Users/name/My Documents" (correct)
     - mkdir /Users/name/My Documents (incorrect - will fail)
     - python "/path/with spaces/script.py" (correct)
     - python /path/with spaces/script.py (incorrect - will fail)
   - After ensuring proper quoting, execute the command.
   - Capture the output of the command.

Usage notes:
  - The command argument is required.
  - You can specify an optional timeout in milliseconds. If not specified, commands will time out after 120000ms (2 minutes).
  - It is very helpful if you write a clear, concise description of what this command does in 5-10 words.
  - If the output exceeds {MAX_OUTPUT_LINES} lines or {MAX_OUTPUT_BYTES} bytes, it will be truncated and the full output will be written to a file.
  - Avoid using Bash with the `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands. Instead, use the dedicated tools: Glob, Grep, Read, Edit, Write.
  - When issuing multiple commands:
    - If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message.
    - If the commands depend on each other, use a single Bash call with '&&' to chain them together.
    - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.
  - AVOID using `cd <directory> && <command>`. Use the `workdir` parameter to change directories instead."""


def get_description_cn(directory: str) -> str:
    """Get Chinese tool description with directory placeholder replaced"""
    return f"""在持久化 Shell 会话中执行 bash 命令，支持可选超时设置，并确保适当的处理和安全措施。

所有命令默认在 {directory} 目录下执行。如果需要在其他目录运行命令，请使用 `workdir` 参数。避免使用 `cd <directory> && <command>` 模式，请改用 `workdir`。

重要：此工具用于 git、npm、docker 等终端操作。不要用它进行文件读写、编辑、搜索或查找等文件操作；请使用专门工具。
Agent 生成的报告、摘要、分析文档、表格、JSON/CSV 导出和其他面向用户的输出文件必须使用 Write 工具写入，以便保存到根会话的 Workspace outputs 目录。不要用 Bash 重定向、tee、Python one-liner 或 shell 脚本创建这些文件。

执行命令前，请遵循以下步骤：

1. 目录验证：
   - 如果命令会创建新目录或文件，先使用 `ls` 验证父目录存在且位置正确
   - 例如，在运行 "mkdir foo/bar" 之前，先使用 `ls foo` 检查 "foo" 存在且是预期父目录

2. 命令执行：
   - 始终用双引号包裹包含空格的文件路径，例如 rm "path with spaces/file.txt"
   - 正确引用示例：
     - mkdir "/Users/name/My Documents"（正确）
     - mkdir /Users/name/My Documents（错误，会失败）
     - python "/path/with spaces/script.py"（正确）
     - python /path/with spaces/script.py（错误，会失败）
   - 确认引用正确后再执行命令。
   - 捕获命令输出。

使用说明：
  - command 参数必填。
  - 可以指定可选 timeout（毫秒）。未指定时，命令会在 120000ms（2 分钟）后超时。
  - 建议提供清晰、简短的 description，用 5-10 个词说明命令作用。
  - 如果输出超过 {MAX_OUTPUT_LINES} 行或 {MAX_OUTPUT_BYTES} 字节，会被截断，完整输出会写入文件。
  - 避免使用 Bash 执行 `find`、`grep`、`cat`、`head`、`tail`、`sed`、`awk` 或 `echo` 命令。请改用专门工具：Glob、Grep、Read、Edit、Write。
  - 发起多个命令时：
    - 如果命令互相独立且可以并行，在单次响应中发起多个 Bash 工具调用。
    - 如果命令之间存在依赖，使用单个 Bash 调用并用 '&&' 串联。
    - 仅当需要顺序执行但不关心前一个命令是否失败时，才使用 ';'
  - 避免使用 `cd <directory> && <command>`。请使用 `workdir` 参数切换目录。"""


def _build_error_message(
    *,
    output: str,
    exit_code: Optional[int],
    timeout_ms: int,
    timed_out: bool,
    aborted: bool,
) -> str:
    """Build a concise failure message from bash execution details."""
    if timed_out:
        return f"Command timed out after {timeout_ms} ms"
    if aborted:
        return "Command was aborted"

    output_text = output.strip()
    if output_text:
        if exit_code is not None:
            return f"Command failed with exit code {exit_code}\n\n{output_text}"
        return output_text

    if exit_code is not None:
        return f"Command failed with exit code {exit_code}"
    return "Command failed"


def get_shell() -> str:
    """Get the appropriate shell for the current platform"""
    if sys.platform == "win32":
        # Prefer PowerShell variants on Windows for better scripting compatibility.
        for shell in ["pwsh", "powershell", "cmd"]:
            if shutil_which(shell):
                return shell
        return "cmd"
    else:
        # Unix-like systems
        return os.environ.get("SHELL", "/bin/bash")


def shutil_which(cmd: str) -> Optional[str]:
    """Cross-platform which command"""
    import shutil

    return shutil.which(cmd)


def _get_windows_shell_command(command: str) -> tuple[str, list[str]]:
    """Build an explicit Windows shell invocation for the command."""
    shell = get_shell()
    if shell in {"pwsh", "powershell"}:
        return shell, [shell, "-NoProfile", "-NonInteractive", "-Command", command]
    return "cmd", ["cmd", "/d", "/s", "/c", command]


def _get_windows_default_workdir_fallback() -> Optional[str]:
    """Return a safe existing directory for Windows host commands."""
    for candidate in (
        os.environ.get("USERPROFILE"),
        os.path.expanduser("~"),
        tempfile.gettempdir(),
        "C:\\",
    ):
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


def _resolve_workdir(base_dir: str, workdir: Optional[str]) -> str:
    """Resolve command working directory, with Windows-only default cwd fallback."""
    cwd = workdir or base_dir

    if not os.path.isabs(cwd):
        cwd = os.path.join(base_dir, cwd)

    if sys.platform == "win32" and workdir is None and not os.path.isdir(cwd):
        if fallback := _get_windows_default_workdir_fallback():
            log.warn("bash.invalid_default_cwd_fallback", {"invalid_cwd": cwd, "fallback": fallback})
            return fallback

    return cwd


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """
    Kill a process and all its children

    Args:
        proc: Process to kill
    """
    try:
        if sys.platform == "win32":
            # Windows: use taskkill
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            # Unix: send SIGTERM to process group
            import signal

            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

            # Wait briefly for graceful shutdown
            await asyncio.sleep(0.1)

            # Force kill if still running
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
    except Exception as e:
        log.warn("kill_process_tree.failed", {"error": str(e)})


def _get_sandbox_config_from_ctx(ctx: ToolContext) -> Optional["BashSandboxConfig"]:
    """从 ToolContext.extra 中提取沙箱配置."""
    sandbox_data = ctx.extra.get("sandbox") if ctx.extra else None
    if not sandbox_data:
        return None

    from flocks.sandbox.types import BashSandboxConfig

    if isinstance(sandbox_data, BashSandboxConfig):
        return sandbox_data
    if isinstance(sandbox_data, dict):
        return BashSandboxConfig(**sandbox_data)
    return None


def _is_elevated_allowed(ctx: ToolContext, tool_name: str) -> bool:
    """Check whether elevated host execution is allowed in sandbox mode."""
    elevated = ctx.extra.get("sandbox_elevated") if ctx.extra else None
    if not isinstance(elevated, dict):
        return False
    if not elevated.get("enabled", False):
        return False
    allowed_tools = elevated.get("tools") or ["bash"]
    return tool_name in allowed_tools

# 输出按会话隔离新增
def _effective_output_session_id(ctx: ToolContext) -> str:
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    for key in ("output_session_id", "main_session_key"):
        value = extra.get(key)
        if value:
            return str(value)
    return ctx.session_id


def _looks_like_generated_document_write(command: str) -> Optional[str]:
    """Detect common shell patterns that create user-facing document outputs."""
    return _looks_like_shell_file_write(command, OUTPUT_FILE_EXTENSIONS)


def _looks_like_temporary_script_write(command: str) -> Optional[str]:
    """Detect shell-created helper scripts that would pollute the project root."""
    return _looks_like_shell_file_write(command, SCRIPT_FILE_EXTENSIONS)


def _looks_like_shell_file_write(command: str, extensions: tuple[str, ...]) -> Optional[str]:
    ext_group = "|".join(extensions)
    path_pattern = (
        rf"(?:"
        rf"/[^\s'\";|&<>]+\.(?:{ext_group})"
        rf"|(?:\./)?(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:{ext_group})"
        rf")"
    )
    patterns = [
        rf"(?:^|[^<])>>?\s*['\"]?(?P<path>{path_pattern})['\"]?",
        rf"\btee(?:\s+-a)?\s+['\"]?(?P<path>{path_pattern})['\"]?",
        rf"\b(?:Out-File|Set-Content|Add-Content)\b[^\n\r;|&]*?(?:-FilePath|-Path)?\s*['\"](?P<path>{path_pattern})['\"]",
        rf"\bopen\(\s*['\"](?P<path>{path_pattern})['\"]\s*,\s*['\"][wa]",
        rf"\bPath\(\s*['\"](?P<path>{path_pattern})['\"]\s*\)\.write_(?:text|bytes)\(",
    ]
    for pattern in patterns:
        match = re.search(pattern, command, flags=re.IGNORECASE)
        if match:
            return match.group("path")
    return None


def _artifacts_dir_for_session(ctx: ToolContext) -> Path:
    from flocks.workspace.manager import WorkspaceManager

    output_dir = WorkspaceManager.get_instance().get_outputs_dir(
        _effective_output_session_id(ctx),
        create=False,
    )
    return output_dir / "artifacts"


def _bash_output_env(ctx: ToolContext) -> dict[str, str]:
    from flocks.workspace.manager import WorkspaceManager

    manager = WorkspaceManager.get_instance()
    output_dir = manager.get_outputs_dir(_effective_output_session_id(ctx), create=False)
    return {
        "FLOCKS_WORKSPACE_DIR": str(manager.get_user_workspace_dir()),
        "FLOCKS_OUTPUTS_DIR": str(output_dir),
        "FLOCKS_ARTIFACTS_DIR": str(output_dir / "artifacts"),
    }


def _workspace_output_scope(ctx: ToolContext) -> Optional[str]:
    from flocks.workspace.manager import WorkspaceManager

    manager = WorkspaceManager.get_instance()
    output_dir = manager.get_outputs_dir(_effective_output_session_id(ctx), create=False)
    outputs_root = manager.get_user_workspace_dir() / "outputs"
    try:
        rel = output_dir.resolve().relative_to(outputs_root.resolve())
    except (OSError, ValueError):
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    return "/".join(part.replace("\\", "/").strip("/") for part in parts[:2] if part)


def _already_session_scoped_output(suffix: str) -> bool:
    parts = [part for part in suffix.strip("/").split("/") if part]
    return (
        len(parts) >= 2
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]) is not None
        and bool(parts[1])
    )


def _normalize_bash_display_paths(ctx: ToolContext, output: str) -> str:
    """Rewrite short sandbox output paths to stable date/session paths."""

    if not output or "/workspace/" not in output:
        return output
    scope = _workspace_output_scope(ctx)
    if not scope:
        return output

    def replace_output(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        suffix = match.group("suffix")
        if _already_session_scoped_output(suffix):
            return match.group(0)
        scheme = "file://" if prefix.startswith("file://") else ""
        return f"{scheme}/workspace/outputs/{scope}{suffix}"

    normalized = re.sub(
        r"(?P<prefix>(?:file://)?/workspace/(?:outputs|output))(?P<suffix>/[^\s'\"<>]+)",
        replace_output,
        output,
    )

    def replace_artifact(match: re.Match[str]) -> str:
        suffix = match.group("suffix")
        scheme = "file://" if match.group("prefix").startswith("file://") else ""
        return f"{scheme}/workspace/outputs/{scope}/artifacts{suffix}"

    return re.sub(
        r"(?P<prefix>(?:file://)?/workspace/artifacts)(?P<suffix>/[^\s'\"<>]+)",
        replace_artifact,
        normalized,
    )


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _migrate_misrouted_project_workspace_outputs(
    ctx: ToolContext,
    source_dir: str,
) -> list[dict[str, str]]:
    from flocks.workspace.manager import WorkspaceManager

    manager = WorkspaceManager.get_instance()
    source_root = Path(source_dir).expanduser() / ".flocks" / "workspace" / "outputs"
    user_outputs_root = manager.get_user_workspace_dir() / "outputs"
    try:
        if source_root.resolve() == user_outputs_root.resolve():
            return []
    except OSError:
        pass
    if not source_root.is_dir():
        return []

    session_id = _effective_output_session_id(ctx)
    session_component = (
        re.sub(r"[^A-Za-z0-9._-]+", "_", str(session_id)).strip("._-")
        or "default-session"
    )
    migrated: list[dict[str, str]] = []

    for day_dir in source_root.iterdir():
        if not day_dir.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_dir.name):
            continue
        session_dir = day_dir / session_component
        if not session_dir.is_dir():
            continue
        target_session_dir = manager.get_outputs_dir(session_id, day=day_dir.name)
        for source_file in sorted(session_dir.rglob("*")):
            if not source_file.is_file():
                continue
            rel_path = source_file.relative_to(session_dir)
            target_file = _next_available_path(target_session_dir / rel_path)
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_file), str(target_file))
            migrated.append({"from": str(source_file), "to": str(target_file)})

        for empty_dir in sorted(session_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if empty_dir.is_dir():
                try:
                    empty_dir.rmdir()
                except OSError:
                    pass
        try:
            session_dir.rmdir()
        except OSError:
            pass

    return migrated


def _migrate_nested_session_outputs(ctx: ToolContext) -> list[dict[str, str]]:
    """Move files back when a script appends date/session to FLOCKS_OUTPUTS_DIR."""
    from flocks.workspace.manager import WorkspaceManager

    manager = WorkspaceManager.get_instance()
    session_id = _effective_output_session_id(ctx)
    session_component = (
        re.sub(r"[^A-Za-z0-9._-]+", "_", str(session_id)).strip("._-")
        or "default-session"
    )
    output_dir = manager.get_outputs_dir(session_id, create=False)
    day_component = output_dir.parent.name
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_component):
        day_component = dt.date.today().isoformat()

    nested_root = output_dir / day_component / session_component
    if not nested_root.is_dir():
        return []

    migrated: list[dict[str, str]] = []
    for source_file in sorted(nested_root.rglob("*")):
        if not source_file.is_file():
            continue
        rel_path = source_file.relative_to(nested_root)
        rel_parts = rel_path.parts
        while (
            len(rel_parts) >= 3
            and rel_parts[0] == day_component
            and rel_parts[1] == session_component
        ):
            rel_parts = rel_parts[2:]
        if not rel_parts:
            continue
        target_file = _next_available_path(output_dir / Path(*rel_parts))
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_file), str(target_file))
        migrated.append({"from": str(source_file), "to": str(target_file)})

    for empty_dir in sorted(nested_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if empty_dir.is_dir():
            try:
                empty_dir.rmdir()
            except OSError:
                pass
    for empty_dir in (nested_root, nested_root.parent):
        try:
            empty_dir.rmdir()
        except OSError:
            pass

    return migrated


def _attach_output_migrations(result: ToolResult, migrations: list[dict[str, str]]) -> ToolResult:
    if not migrations:
        return result
    result.metadata["migrated_outputs"] = migrations
    lines = ["", "<migrated_outputs>"]
    for item in migrations:
        lines.append(f"{item['from']} -> {item['to']}")
    lines.append("</migrated_outputs>")
    result.output = (result.output or "") + "\n".join(lines)
    return result


def _is_allowed_temporary_script_path(path: str, cwd: str, ctx: ToolContext) -> bool:
    normalized = str(path).replace("\\", "/").strip("'\"")
    if normalized.startswith(("/tmp/", "/var/tmp/", "/workspace/artifacts/")):
        return True

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd) / candidate

    allowed_roots = [Path(tempfile.gettempdir()), _artifacts_dir_for_session(ctx)]
    for root in allowed_roots:
        try:
            if candidate.resolve().is_relative_to(root.resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def _is_flocks_plugin_write_path(path: str, base_dir: str) -> bool:
    """Return True when a shell write targets a project Flocks plugin definition."""
    normalized = str(path).replace("\\", "/").strip("'\"")
    if normalized.startswith((".flocks/plugins/", "./.flocks/plugins/")):
        return True
    if normalized.startswith("/workspace/.flocks/plugins/"):
        return True

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(base_dir) / candidate

    roots = [Path(base_dir).expanduser() / ".flocks" / "plugins", Path.cwd() / ".flocks" / "plugins"]
    for root in roots:
        try:
            candidate_resolved = candidate.resolve()
            root_resolved = root.resolve()
        except OSError:
            candidate_resolved = candidate.absolute()
            root_resolved = root.absolute()
        try:
            if candidate_resolved.is_relative_to(root_resolved):
                return True
        except ValueError:
            continue
    return False


def _is_user_flocks_plugin_write_path(path: str) -> bool:
    """Return True when a shell write targets ~/.flocks/plugins."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return False
    root = Path.home() / ".flocks" / "plugins"
    try:
        return candidate.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False
#------------------------------end--------------------------------


def _path_is_within(path: str | Path, root: str | Path) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(Path(root).expanduser().resolve())
        return True
    except (OSError, ValueError):
        return False


def _sandbox_project_plugins_host_root(sandbox: "BashSandboxConfig") -> Optional[Path]:
    if sandbox.project_plugins_dir:
        return Path(sandbox.project_plugins_dir).expanduser()
    if sandbox.agent_workspace_dir:
        return Path(sandbox.agent_workspace_dir).expanduser() / ".flocks" / "plugins"
    return None


def _sandbox_project_plugins_container_root(sandbox: "BashSandboxConfig") -> str:
    container_root = sandbox.container_workdir.replace("\\", "/").rstrip("/") or "/workspace"
    return f"{container_root}/.flocks/plugins"


def _host_path_variants(path: Path) -> list[str]:
    raw = str(path)
    variants = [raw, raw.replace("\\", "/"), path.as_posix()]
    result: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        if variant and variant not in seen:
            seen.add(variant)
            result.append(variant)
    return result


def _normalize_container_plugin_paths(command: str, container_root: str) -> str:
    pattern = re.compile(
        re.escape(container_root.rstrip("/")) + r"(?P<suffix>(?:[\\/][^\s'\";|&<>]+)*)"
    )

    def repl(match: re.Match[str]) -> str:
        return container_root.rstrip("/") + match.group("suffix").replace("\\", "/")

    return pattern.sub(repl, command)


def _rewrite_project_plugin_host_paths_for_sandbox(
    command: str,
    sandbox: "BashSandboxConfig",
) -> str:
    """Map host project-plugin paths in shell commands to container paths."""

    host_root = _sandbox_project_plugins_host_root(sandbox)
    if host_root is None:
        return command

    container_root = _sandbox_project_plugins_container_root(sandbox)
    rewritten = command
    for variant in sorted(_host_path_variants(host_root), key=len, reverse=True):
        rewritten = rewritten.replace(variant, container_root)
    return _normalize_container_plugin_paths(rewritten, container_root)


def _map_project_plugin_workdir(
    workdir: str,
    sandbox: "BashSandboxConfig",
) -> Optional[tuple[str, str]]:
    host_root = _sandbox_project_plugins_host_root(sandbox)
    if host_root is None:
        return None

    container_root = _sandbox_project_plugins_container_root(sandbox)
    raw = str(workdir).replace("\\", "/")
    if raw.startswith("file://"):
        raw = raw[7:]

    if raw == container_root or raw.startswith(container_root.rstrip("/") + "/"):
        rel = raw[len(container_root.rstrip("/")) :].lstrip("/")
        host_workdir = host_root.joinpath(*[part for part in rel.split("/") if part])
        if os.path.isdir(host_workdir):
            return str(host_workdir), f"{container_root}/{rel}" if rel else container_root
        return None

    candidate = Path(workdir[7:] if str(workdir).startswith("file://") else workdir).expanduser()
    if not candidate.is_absolute():
        return None
    if not _path_is_within(candidate, host_root) or not os.path.isdir(candidate):
        return None
    try:
        rel_path = candidate.resolve().relative_to(host_root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    container_workdir = f"{container_root}/{rel_path}" if rel_path else container_root
    return str(candidate), container_workdir


async def _resolve_sandbox_workdir(
    workdir: str,
    sandbox: "BashSandboxConfig",
) -> tuple[str, str]:
    """
    解析沙箱内的工作目录。

    对齐 OpenClaw resolveSandboxWorkdir (bash-tools.shared.ts)。

    Returns:
        (host_workdir, container_workdir)
    """
    from flocks.sandbox.paths import assert_sandbox_path

    fallback = sandbox.workspace_dir
    plugin_workdir = _map_project_plugin_workdir(workdir, sandbox)
    if plugin_workdir is not None:
        return plugin_workdir

    try:
        result = await assert_sandbox_path(
            file_path=workdir,
            cwd=os.getcwd(),
            root=sandbox.workspace_dir,
        )
        if not os.path.isdir(result.resolved):
            raise ValueError("workdir is not a directory")

        # 将相对路径映射为容器路径
        relative = result.relative.replace(os.sep, "/") if result.relative else ""
        container_workdir = (
            f"{sandbox.container_workdir}/{relative}"
            if relative
            else sandbox.container_workdir
        )
        return result.resolved, container_workdir
    except (ValueError, OSError):
        return fallback, sandbox.container_workdir


@ToolRegistry.register_function(
    name="bash",
    description=get_description(os.getcwd()),
    description_cn=get_description_cn(os.getcwd()),
    category=ToolCategory.TERMINAL,
    parameters=[
        ToolParameter(name="command", type=ParameterType.STRING, description="The command to execute", required=True),
        ToolParameter(
            name="timeout",
            type=ParameterType.INTEGER,
            description="Optional timeout in milliseconds",
            required=False,
            default=DEFAULT_TIMEOUT_MS
        ),
        ToolParameter(
            name="workdir",
            type=ParameterType.STRING,
            description="The working directory to run the command in. Defaults to project directory.",
            required=False
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="Clear, concise description of what this command does in 5-10 words",
            required=False
        ),
        ToolParameter(
            name="host",
            type=ParameterType.STRING,
            description="Execution host override: 'sandbox' (default) or 'host' (elevated when sandbox is active)",
            required=False,
            enum=["sandbox", "host"],
        ),
    ]
)
async def bash_tool(
    ctx: ToolContext,
    command: str,
    timeout: Optional[int] = None,
    workdir: Optional[str] = None,
    description: Optional[str] = None,
    host: Optional[str] = None,
) -> ToolResult:
    """
    Execute a bash command.

    Supports two execution paths:
    1. Host execution (default) - directly on the host machine
    2. Sandbox execution - inside a Docker container (when sandbox config is present)
    """
    # Resolve working directory
    base_dir = Instance.get_directory() or os.getcwd()
    # 输出按会话隔离修改
    # 删除
    # cwd = _resolve_workdir(base_dir, workdir)
    # 新增
    cwd = workdir or base_dir
    if not os.path.isabs(cwd):
        cwd = os.path.join(base_dir, cwd)
    # Validate timeout
    timeout_ms = timeout or DEFAULT_TIMEOUT_MS
    if timeout_ms < 0:
        return ToolResult(
            success=False, error=f"Invalid timeout value: {timeout_ms}. Timeout must be a positive number."
        )

    timeout_sec = timeout_ms / 1000

    from flocks.tool.code.bash_blacklist import check_bash_blacklist

    blocked_command = await check_bash_blacklist(command)
    if blocked_command:
        return ToolResult(
            success=False,
            error=blocked_command.message,
            title=description or command,
            metadata={
                "blocked_by_bash_blacklist": True,
                "blocked_command": blocked_command.command,
                "blacklist_rule": blocked_command.rule,
            },
        )

    # 输出按会话隔离新增
    blocked_output_path = _looks_like_generated_document_write(command)
    blocked_script_path = _looks_like_temporary_script_write(command)
    if (
        blocked_script_path
        and not _is_flocks_plugin_write_path(blocked_script_path, cwd)
        and not _is_allowed_temporary_script_path(blocked_script_path, cwd, ctx)
    ):
        artifacts_dir = _artifacts_dir_for_session(ctx)
        return ToolResult(
            success=False,
            error=(
                "Temporary helper scripts must not be created in the project directory with Bash. "
                f"Detected attempted script path: {blocked_script_path}. "
                f"Use /tmp for throwaway scripts, or Write with a filePath under: {artifacts_dir}"
            ),
            title=description or command,
            metadata={
                "blocked_temporary_script_write": True,
                "detected_path": blocked_script_path,
                "expected_artifacts_dir": str(artifacts_dir),
            },
        )
    if blocked_output_path and _is_user_flocks_plugin_write_path(blocked_output_path):
        expected_plugin_dir = Path(cwd) / ".flocks" / "plugins"
        return ToolResult(
            success=False,
            error=(
                "Flocks plugin definitions must be written to the project-level "
                f"plugin directory, not ~/.flocks/plugins. Detected attempted path: {blocked_output_path}. "
                f"Use Write with a filePath under: {expected_plugin_dir}"
            ),
            title=description or command,
            metadata={
                "blocked_user_plugin_write": True,
                "detected_path": blocked_output_path,
                "expected_plugin_dir": str(expected_plugin_dir),
            },
        )
    if blocked_output_path and not _is_flocks_plugin_write_path(blocked_output_path, cwd):
        from flocks.workspace.manager import WorkspaceManager

        output_dir = WorkspaceManager.get_instance().get_outputs_dir(
            _effective_output_session_id(ctx),
            create=False,
        )
        return ToolResult(
            success=False,
            error=(
                "Generated documents must be written with the Write tool, not Bash, "
                "so they are saved under the root session Workspace outputs directory. "
                f"Detected attempted output path: {blocked_output_path}. "
                f"Use Write with a filePath under: {output_dir}"
            ),
            title=description or command,
            metadata={
                "blocked_generated_document_write": True,
                "detected_path": blocked_output_path,
                "expected_output_dir": str(output_dir),
            },
        )
    # -------------------------end------------------------------------------------

    # Check for sandbox configuration
    sandbox = _get_sandbox_config_from_ctx(ctx)

    if sandbox:
        desired_host = (host or "sandbox").strip().lower()
        if desired_host == "host":
            if not _is_elevated_allowed(ctx, "bash"):
                return ToolResult(
                    success=False,
                    error=(
                        "Elevated host execution is not allowed for bash in current sandbox policy. "
                        "Enable sandbox.elevated.enabled and include 'bash' in sandbox.elevated.tools."
                    ),
                    title=description or command,
                    metadata={"sandbox": True, "elevated_requested": True},
                )
            return await _execute_host(
                ctx=ctx,
                command=command,
                cwd=cwd,
                timeout_sec=timeout_sec,
                timeout_ms=timeout_ms,
                description=description,
                extra_metadata={"sandbox": True, "elevated": True},
            )
        return await _execute_sandboxed(
            ctx=ctx,
            command=command,
            cwd=cwd,
            sandbox=sandbox,
            timeout_sec=timeout_sec,
            timeout_ms=timeout_ms,
            description=description,
        )
    else:
        return await _execute_host(
            ctx=ctx,
            command=command,
            cwd=cwd,
            timeout_sec=timeout_sec,
            timeout_ms=timeout_ms,
            description=description,
        )


async def _execute_host(
    ctx: ToolContext,
    command: str,
    cwd: str,
    timeout_sec: float,
    timeout_ms: int,
    description: Optional[str],
    extra_metadata: Optional[dict] = None,
) -> ToolResult:
    """在宿主机上执行命令（原有逻辑）."""
    # Check if working directory is outside project
    if not Instance.contains_path(cwd):
        await ctx.ask(permission="external_directory", patterns=[cwd], always=[os.path.dirname(cwd) + "*"], metadata={})

    risk = classify_shell_risk(command)
    if risk:
        return _high_risk_shell_result(command, description, risk, extra_metadata)

    await _ask_bash_permission(ctx, command, sandbox=False)

    # Get shell
    shell = get_shell()

    # Initialize metadata
    ctx.metadata(
        {
            "metadata": {
                "output": "",
                "description": description or command,
                **(extra_metadata or {}),
            }
        }
    )

    env = os.environ.copy()
    env.update(_bash_output_env(ctx))
    if sys.platform == "win32":
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

    # Execute command
    try:
        if sys.platform == "win32":
            shell_name, shell_cmd = _get_windows_shell_command(command)
            log.info(
                "bash.execute.host",
                {"command": command, "cwd": cwd, "shell": shell_name, "shell_cmd": shell_cmd[:-1]},
            )
            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        else:
            log.info("bash.execute.host", {"command": command, "cwd": cwd, "shell": shell})
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,  # Create new process group
            )
    except Exception as e:
        return ToolResult(success=False, error=f"Failed to start command: {str(e)}", title=description or command)

    result = await _stream_output(
        ctx=ctx,
        proc=proc,
        command=command,
        timeout_sec=timeout_sec,
        timeout_ms=timeout_ms,
        description=description,
        extra_metadata=extra_metadata,
    )
    migrations = _migrate_misrouted_project_workspace_outputs(
        ctx,
        Instance.get_directory() or cwd,
    )
    migrations.extend(_migrate_nested_session_outputs(ctx))
    return _attach_output_migrations(result, migrations)


def _high_risk_shell_result(
    command: str,
    description: Optional[str],
    risk,
    extra_metadata: Optional[dict] = None,
) -> ToolResult:
    metadata = {
        "blocked_by_high_risk_shell": True,
        "command": command,
        **risk.metadata(),
        **(extra_metadata or {}),
    }
    return ToolResult(
        success=False,
        error=high_risk_block_message(risk),
        title=description or command,
        metadata=metadata,
    )


async def _ask_bash_permission(ctx: ToolContext, command: str, *, sandbox: bool) -> None:
    metadata = {"sandbox": True} if sandbox else {}
    await ctx.ask(permission="bash", patterns=[command], always=["*"], metadata=metadata)


async def _execute_sandboxed(
    ctx: ToolContext,
    command: str,
    cwd: str,
    sandbox: "BashSandboxConfig",
    timeout_sec: float,
    timeout_ms: int,
    description: Optional[str],
) -> ToolResult:
    """
    在沙箱容器内执行命令。

    对齐 OpenClaw bash-tools.exec.ts sandbox 路径:
    - 使用 docker exec 在容器内运行
    - 路径映射 host → container
    - 构建隔离环境变量
    """
    from flocks.sandbox.docker import build_docker_exec_args, build_sandbox_env

    command = _rewrite_project_plugin_host_paths_for_sandbox(command, sandbox)

    log.info(
        "bash.execute.sandbox",
        {
            "command": command,
            "container": sandbox.container_name,
        },
    )

    risk = classify_shell_risk(command)
    if risk:
        return _high_risk_shell_result(
            command,
            description,
            risk,
            {"sandbox": True, "container": sandbox.container_name},
        )

    await _ask_bash_permission(ctx, command, sandbox=True)

    # Initialize metadata
    ctx.metadata(
        {
            "metadata": {
                "output": "",
                "description": description or command,
                "sandbox": True,
                "container": sandbox.container_name,
            }
        }
    )

    # 解析工作目录 (host → container 路径映射)
    host_workdir, container_workdir = await _resolve_sandbox_workdir(cwd, sandbox)

    # 构建沙箱环境变量
    env = build_sandbox_env(
        default_path=DEFAULT_PATH,
        sandbox_env=sandbox.env,
        container_workdir=container_workdir,
    )

    # 构建 docker exec 参数
    docker_args = build_docker_exec_args(
        container_name=sandbox.container_name,
        command=command,
        workdir=container_workdir,
        env=env,
        tty=False,
    )

    # 使用 docker exec 执行
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *docker_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=host_workdir,
            start_new_session=True if sys.platform != "win32" else False,
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Failed to start sandboxed command: {str(e)}",
            title=description or command,
            metadata={"sandbox": True, "container": sandbox.container_name},
        )

    result = await _stream_output(
        ctx=ctx,
        proc=proc,
        command=command,
        timeout_sec=timeout_sec,
        timeout_ms=timeout_ms,
        description=description,
        extra_metadata={"sandbox": True, "container": sandbox.container_name},
    )
    migrations = _migrate_misrouted_project_workspace_outputs(ctx, sandbox.workspace_dir)
    migrations.extend(_migrate_nested_session_outputs(ctx))
    return _attach_output_migrations(result, migrations)


async def _stream_output(
    ctx: ToolContext,
    proc: asyncio.subprocess.Process,
    command: str,
    timeout_sec: float,
    timeout_ms: int,
    description: Optional[str],
    extra_metadata: Optional[dict] = None,
) -> ToolResult:
    """流式读取进程输出并返回结果（host 和 sandbox 共用）."""
    output = ""
    timed_out = False
    aborted = False

    async def read_output():
        nonlocal output
        while True:
            # Read from both stdout and stderr
            stdout_task = asyncio.create_task(proc.stdout.read(4096))
            stderr_task = asyncio.create_task(proc.stderr.read(4096))

            done, pending = await asyncio.wait([stdout_task, stderr_task], return_when=asyncio.FIRST_COMPLETED)

            for task in pending:
                task.cancel()

            for task in done:
                try:
                    chunk = task.result()
                    if chunk:
                        output += chunk.decode("utf-8", errors="replace")

                        # Update metadata with truncated output
                        truncated_output = output
                        if len(truncated_output) > MAX_METADATA_LENGTH:
                            truncated_output = truncated_output[:MAX_METADATA_LENGTH] + "\n\n..."

                        ctx.metadata(
                            {
                                "metadata": {
                                    "output": truncated_output,
                                    "description": description or command,
                                    **(extra_metadata or {}),
                                }
                            }
                        )
                except asyncio.CancelledError:
                    pass

            # Check if process has exited
            if proc.returncode is not None:
                # Read any remaining output
                remaining_stdout = await proc.stdout.read()
                remaining_stderr = await proc.stderr.read()
                if remaining_stdout:
                    output += remaining_stdout.decode("utf-8", errors="replace")
                if remaining_stderr:
                    output += remaining_stderr.decode("utf-8", errors="replace")
                break

            # Check for abort
            if ctx.aborted:
                break

    # Create tasks
    read_task = asyncio.create_task(read_output())

    try:
        await asyncio.wait_for(read_task, timeout=timeout_sec)
    except asyncio.TimeoutError:
        timed_out = True
        read_task.cancel()

    # Check for abort
    finally:
        # 无论正常退出、超时、还是 ctx.aborted 触发的异常        
        # 统一强制杀掉进程组内的所有残留进程（如用 & 启动的后台任务）
        await kill_process_tree(proc)
    
    if ctx.aborted:        
        aborted = True

    # Wait for process to finish
    try:
        await asyncio.wait_for(proc.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass

    exit_code = proc.returncode

    # Build result metadata
    result_metadata = []
    if timed_out:
        result_metadata.append(f"bash tool terminated command after exceeding timeout {timeout_ms} ms")
    if aborted:
        result_metadata.append("User aborted the command")

    if result_metadata:
        output += "\n\n<bash_metadata>\n" + "\n".join(result_metadata) + "\n</bash_metadata>"

    output = _normalize_bash_display_paths(ctx, output)

    # Truncate output for metadata
    truncated_output = output
    if len(truncated_output) > MAX_METADATA_LENGTH:
        truncated_output = truncated_output[:MAX_METADATA_LENGTH] + "\n\n..."

    # Determine success based on exit code
    success = exit_code == 0 if exit_code is not None else not timed_out and not aborted
    error_message = None
    if not success:
        error_message = _build_error_message(
            output=truncated_output,
            exit_code=exit_code,
            timeout_ms=timeout_ms,
            timed_out=timed_out,
            aborted=aborted,
        )

    return ToolResult(
        success=success,
        output=output,
        error=error_message,
        title=description or command,
        metadata={
            "output": truncated_output,
            "exit": exit_code,
            "description": description or command,
            "timed_out": timed_out,
            "aborted": aborted,
            **(extra_metadata or {}),
        },
    )
