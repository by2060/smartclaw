"""
Workspace Manager

Manages ~/.flocks/workspace/ - the user-facing file storage for:
- outputs/     : agent-generated task artifacts
- knowledge/   : user-curated knowledge base (future: vector indexing)

Memory files stay in ~/.flocks/data/memory/ (agent-managed, not migrated).
This manager provides a read-only view into data/memory/ for the WebUI.
"""

import datetime as dt
import os
import re
from pathlib import Path
from typing import Optional

from flocks.utils.log import Log

log = Log.create(service="workspace.manager")

# Extensions treated as plain-text (previewable + editable in WebUI).
# Note: dotfiles like .gitignore have suffix='' in Python, so they are NOT
# matched here; they will fall through to the binary-file path (download only).
TEXT_EXTENSIONS = {
    ".md", ".txt", ".log", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".py", ".js", ".ts",
    ".sh", ".bash", ".csv", ".xml", ".html", ".css",
    ".tsx", ".jsx", ".env",
    ".sql", ".rs", ".go", ".java", ".c", ".cpp", ".h",
}

# Conventional subdirectories (created on init, not enforced)
CONVENTION_DIRS = ["outputs", "knowledge"]

# 输出按会话隔离新增
def _safe_path_component(value: Optional[str], fallback: str) -> str:
    """Return a filesystem-safe single path component."""
    if not value:
        return fallback
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._-")
    return component or fallback


def _looks_like_session_component(value: str) -> bool:
    """Return True for Flocks session directory names."""
    return bool(re.fullmatch(r"ses_[A-Za-z0-9._-]+", str(value)))


def _user_home_dir() -> Path:
    """Return the OS account home, ignoring a HOME value pointed at the project."""
    if os.name != "nt":
        try:
            import pwd

            home = pwd.getpwuid(os.getuid()).pw_dir
            if home:
                return Path(home)
        except Exception:
            pass
    return Path.home()


def _user_workspace_dir() -> Path:
    return _user_home_dir() / ".flocks" / "workspace"


def _looks_like_source_dir(path: Path) -> bool:
    try:
        candidate = path.expanduser().resolve()
    except OSError:
        candidate = path.expanduser().absolute()

    try:
        cwd = Path.cwd().resolve()
    except OSError:
        cwd = Path.cwd().absolute()
    if candidate == cwd:
        return True

    user_workspace = _user_workspace_dir()
    try:
        is_user_workspace = candidate == user_workspace.resolve()
    except OSError:
        is_user_workspace = candidate == user_workspace.absolute()
    if candidate.name == "flocks" and not is_user_workspace:
        return True

    source_markers = ("pyproject.toml", "AGENTS.md", "CLAUDE.md", "CONTEXT.md")
    if any((candidate / marker).exists() for marker in source_markers):
        return True
    if (candidate / "flocks").is_dir() and (candidate / ".flocks").is_dir():
        return True

    if candidate.name == "workspace" and candidate.parent.name == ".flocks":
        project_root = candidate.parent.parent
        return _looks_like_source_dir(project_root)

    return False
# ------------------------end----------------------------

def _get_workspace_dir() -> Path:
    """
    Resolve workspace directory.

    Priority:
    1. FLOCKS_WORKSPACE_DIR environment variable
    2. ~/.flocks/workspace (default, adjacent to data/ logs/ plugins/)
    """
    override = os.getenv("FLOCKS_WORKSPACE_DIR")
    # 输出按会话隔离修改
    # 删除
    '''
        if override:
        return Path(override)
    '''
    # 新增
    if override:
        candidate = Path(override).expanduser()
        if _looks_like_source_dir(candidate):
            return _user_workspace_dir()
        return candidate
    # -------------end ----------------------
    from flocks.config.config import Config
    # data_dir is ~/.flocks/data; workspace is sibling of data/
    # 输出按会话隔离修改
    # 删除
    # return Config.get_data_path().parent / "workspace"
    # 新增
    candidate = Config.get_data_path().parent / "workspace"
    if _looks_like_source_dir(candidate):
        return _user_workspace_dir()
    return candidate
    # -----------------------end--------------------------


class WorkspaceManager:
    """
    Singleton manager for the workspace directory.

    All path arguments accepted by public methods are relative to the
    workspace root (or memory root for memory methods).  Absolute paths
    are rejected to prevent path traversal attacks.
    """

    _instance: Optional["WorkspaceManager"] = None

    def __init__(self) -> None:
        self._workspace_dir: Optional[Path] = None
        self._memory_dir: Optional[Path] = None
        self._dirs_ensured: bool = False

    @classmethod
    def get_instance(cls) -> "WorkspaceManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ #
    # Directory resolution
    # ------------------------------------------------------------------ #

    def get_workspace_dir(self) -> Path:
        if self._workspace_dir is None:
            self._workspace_dir = _get_workspace_dir()
        return self._workspace_dir

    # 输出按会话隔离新增
    def get_user_workspace_dir(self) -> Path:
        return _user_workspace_dir()
    # ------------end------------------------

    def get_memory_dir(self) -> Path:
        """Return path to agent-managed memory directory (read-only view)."""
        if self._memory_dir is None:
            from flocks.config.config import Config
            self._memory_dir = Config.get_data_path() / "memory"
        return self._memory_dir

    # 输出按会话隔离新增
    def get_outputs_dir(
        self,
        session_id: Optional[str] = None,
        *,
        day: Optional[str | dt.date] = None,
        create: bool = True,
    ) -> Path:
        """Return the default agent-output directory for a session.

        Output files are organized as:
        ``~/.flocks/workspace/outputs/<YYYY-MM-DD>/<session_id>/``.
        """
        if isinstance(day, dt.date):
            day_component = day.isoformat()
        else:
            day_component = day or dt.date.today().isoformat()
        day_component = _safe_path_component(day_component, dt.date.today().isoformat())
        session_component = _safe_path_component(session_id, "default-session")

        output_dir = self.get_user_workspace_dir() / "outputs" / day_component / session_component
        if create:
            output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def rewrite_legacy_output_path(
        self,
        file_path: str | Path,
        session_id: Optional[str],
        source_dir: Optional[str | Path] = None,
    ) -> Optional[Path]:
        """Map legacy output locations to outputs/<date>/<session>/."""

        workspace = self.get_user_workspace_dir().resolve()
        outputs_root = (workspace / "outputs").resolve()
        path = Path(file_path).expanduser()

        session_component = _safe_path_component(session_id, "default-session")

        def rewrite_parts(
            rel_parts: tuple[str, ...],
            *,
            allow_already_scoped: bool,
            output_workspace: Optional[Path] = None,
        ) -> Optional[Path]:
            if not rel_parts:
                return None

            def output_dir(day_value: Optional[str] = None) -> Path:
                if output_workspace is None:
                    return self.get_outputs_dir(session_id, day=day_value)
                day_component = _safe_path_component(
                    day_value or dt.date.today().isoformat(),
                    dt.date.today().isoformat(),
                )
                return output_workspace / "outputs" / day_component / session_component

            day_component = rel_parts[0]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_component):
                if len(rel_parts) < 2:
                    return None
                if len(rel_parts) >= 2 and rel_parts[1] == session_component:
                    if allow_already_scoped:
                        return None
                    tail_parts = rel_parts[2:]
                elif len(rel_parts) >= 2 and _looks_like_session_component(rel_parts[1]):
                    tail_parts = rel_parts[2:]
                else:
                    tail_parts = rel_parts[1:]
                return output_dir(day_component) / Path(*tail_parts)

            return output_dir() / Path(*rel_parts)

        def rewrite_user_workspace_outputs_if_needed() -> Optional[Path]:
            user_outputs_root = (_user_workspace_dir() / "outputs").resolve()
            if not resolved.is_relative_to(user_outputs_root):
                return None
            return rewrite_parts(
                resolved.relative_to(user_outputs_root).parts,
                allow_already_scoped=True,
                output_workspace=_user_workspace_dir(),
            )

        def rewrite_project_dated_outputs() -> Optional[Path]:
            if "outputs" not in raw_parts:
                return None
            idx = raw_parts.index("outputs")
            if len(raw_parts) <= idx + 2:
                return None
            rel_parts = tuple(raw_parts[idx + 1:])
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rel_parts[0]):
                return None
            outputs_parent = Path(*raw_parts[:idx])
            if not _looks_like_source_dir(outputs_parent):
                return None
            return rewrite_parts(
                rel_parts,
                allow_already_scoped=False,
                output_workspace=_user_workspace_dir(),
            )

        raw_parts = path.parts
        if ".flocks_outputs" in raw_parts:
            idx = raw_parts.index(".flocks_outputs")
            rewritten = rewrite_parts(
                tuple(raw_parts[idx + 1:]),
                allow_already_scoped=False,
            )
            if rewritten is not None:
                return rewritten

        if not path.is_absolute():
            return None

        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()

        rewritten_user_output = rewrite_user_workspace_outputs_if_needed()
        if rewritten_user_output is not None:
            return rewritten_user_output

        if resolved.is_relative_to(outputs_root):
            rewritten = rewrite_parts(
                resolved.relative_to(outputs_root).parts,
                allow_already_scoped=True,
            )
            if rewritten is not None:
                return rewritten

        rewritten_project_output = rewrite_project_dated_outputs()
        if rewritten_project_output is not None:
            return rewritten_project_output

        if source_dir is None:
            return None

        source_outputs_root = (Path(source_dir).expanduser().resolve() / "outputs").resolve()
        if not resolved.is_relative_to(source_outputs_root):
            return None

        return rewrite_parts(
            resolved.relative_to(source_outputs_root).parts,
            allow_already_scoped=False,
        )
    # ------------------------------end--------------------------------

    def ensure_dirs(self) -> None:
        """Create workspace root and conventional subdirectories if absent.

        Idempotent: a boolean flag prevents redundant syscalls after the
        first successful call within the same process lifetime.
        """
        if self._dirs_ensured:
            return
        workspace = self.get_workspace_dir()
        workspace.mkdir(parents=True, exist_ok=True)
        for name in CONVENTION_DIRS:
            (workspace / name).mkdir(exist_ok=True)
        self._dirs_ensured = True
        log.info("workspace.dirs.ensured", {"path": str(workspace)})

    # ------------------------------------------------------------------ #
    # Path safety
    # ------------------------------------------------------------------ #

    def resolve_workspace_path(self, rel_path: str) -> Path:
        """
        Resolve a relative path inside the workspace root.

        Raises ValueError if the resolved path escapes the workspace.
        Uses Path.is_relative_to() (Python 3.9+) to avoid the prefix-match
        pitfall where '/tmp/ws_evil' would wrongly pass a startswith check
        against '/tmp/ws'.
        """
        workspace = self.get_workspace_dir().resolve()
        if Path(rel_path).is_absolute():
            raise ValueError(f"Absolute paths not allowed: {rel_path}")
        resolved = (workspace / rel_path).resolve()
        if not resolved.is_relative_to(workspace):
            raise ValueError(f"Path traversal detected: {rel_path}")
        return resolved

    def resolve_memory_path(self, rel_path: str) -> Path:
        """
        Resolve a relative path inside the memory root (read-only).

        Raises ValueError if the resolved path escapes memory root.
        Uses Path.is_relative_to() (Python 3.9+) for safe boundary checks.
        """
        memory = self.get_memory_dir().resolve()
        if Path(rel_path).is_absolute():
            raise ValueError(f"Absolute paths not allowed: {rel_path}")
        resolved = (memory / rel_path).resolve()
        if not resolved.is_relative_to(memory):
            raise ValueError(f"Path traversal detected: {rel_path}")
        return resolved

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def is_text_file(path: Path) -> bool:
        return path.suffix.lower() in TEXT_EXTENSIONS
