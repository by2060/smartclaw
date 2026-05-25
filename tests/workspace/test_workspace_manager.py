"""
Unit tests for WorkspaceManager

Tests path resolution, directory management, text-file detection,
and security (path-traversal prevention).
"""

from pathlib import Path

import pytest


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Provide an isolated workspace directory via FLOCKS_WORKSPACE_DIR.

    Also overrides FLOCKS_DATA_DIR so the memory-dir view never touches ~/.flocks.
    """
    ws = tmp_path / "workspace"
    data = tmp_path / "data"
    ws.mkdir()
    data.mkdir()
    (data / "memory").mkdir()

    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(ws))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data))

    # Reset both singletons so env vars are re-read
    from flocks.workspace.manager import WorkspaceManager
    from flocks.config.config import Config
    WorkspaceManager._instance = None
    Config._global_config = None

    yield ws

    WorkspaceManager._instance = None
    Config._global_config = None


@pytest.fixture()
def manager(tmp_workspace: Path):
    from flocks.workspace.manager import WorkspaceManager
    mgr = WorkspaceManager.get_instance()
    mgr.ensure_dirs()
    return mgr


# ─── Singleton ───────────────────────────────────────────────────────────────

class TestSingleton:
    def test_same_instance_returned(self, tmp_workspace: Path):
        from flocks.workspace.manager import WorkspaceManager
        a = WorkspaceManager.get_instance()
        b = WorkspaceManager.get_instance()
        assert a is b

    def test_instance_reset(self, tmp_workspace: Path):
        from flocks.workspace.manager import WorkspaceManager
        a = WorkspaceManager.get_instance()
        WorkspaceManager._instance = None
        b = WorkspaceManager.get_instance()
        assert a is not b


# ─── Directory paths ──────────────────────────────────────────────────────────

class TestDirectoryPaths:
    def test_get_workspace_dir_respects_env(self, tmp_workspace: Path, manager):
        assert manager.get_workspace_dir() == tmp_workspace

    def test_workspace_dir_env_source_root_falls_back_to_user_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        project = tmp_path / "project"
        home = tmp_path / "home"
        project.mkdir()
        home.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname = \"demo\"\n")
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(project))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            assert manager.get_workspace_dir() == home / ".flocks" / "workspace"
            assert (
                manager.get_outputs_dir("ses_same", day="2026-05-07", create=False)
                == home / ".flocks" / "workspace" / "outputs" / "2026-05-07" / "ses_same"
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_workspace_dir_named_flocks_falls_back_to_user_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        deploy_root = tmp_path / "opt" / "zhhtest" / "flocks"
        home = tmp_path / "home"
        deploy_root.mkdir(parents=True)
        home.mkdir()
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(deploy_root))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            assert manager.get_workspace_dir() == home / ".flocks" / "workspace"
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_home_env_pointing_at_project_does_not_define_user_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        deploy_root = tmp_path / "opt" / "zhhtest" / "flocks"
        real_home = tmp_path / "root"
        deploy_root.mkdir(parents=True)
        real_home.mkdir()
        monkeypatch.setenv("HOME", str(deploy_root))
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(deploy_root / ".flocks" / "workspace"))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: real_home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            assert manager.get_workspace_dir() == real_home / ".flocks" / "workspace"
            assert (
                manager.get_outputs_dir("ses_abc123", day="2026-05-07", create=False)
                == real_home
                / ".flocks"
                / "workspace"
                / "outputs"
                / "2026-05-07"
                / "ses_abc123"
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_project_flocks_workspace_falls_back_to_user_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        project = tmp_path / "project"
        home = tmp_path / "home"
        project_workspace = project / ".flocks" / "workspace"
        project_workspace.mkdir(parents=True)
        home.mkdir()
        (project / "AGENTS.md").write_text("# rules\n")
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(project_workspace))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            assert manager.get_workspace_dir() == home / ".flocks" / "workspace"
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_project_flocks_dir_falls_back_to_user_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        project = tmp_path / "project"
        home = tmp_path / "home"
        project_flocks = project / ".flocks"
        project_flocks.mkdir(parents=True)
        home.mkdir()
        (project / "AGENTS.md").write_text("# rules\n")
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(project_flocks))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            assert manager.get_workspace_dir() == home / ".flocks" / "workspace"
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_project_uploads_dir_falls_back_to_user_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        project = tmp_path / "project"
        home = tmp_path / "home"
        project_uploads = project / "uploads"
        project_uploads.mkdir(parents=True)
        home.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname = \"demo\"\n")
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(project_uploads))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            assert manager.get_workspace_dir() == home / ".flocks" / "workspace"
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_cached_project_workspace_is_revalidated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        project = tmp_path / "project"
        home = tmp_path / "home"
        project.mkdir()
        home.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname = \"demo\"\n")
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            manager._workspace_dir = project
            assert manager.get_workspace_dir() == home / ".flocks" / "workspace"
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_resolve_user_workspace_path_ignores_project_workspace_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        project = tmp_path / "project"
        home = tmp_path / "home"
        project.mkdir()
        home.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname = \"demo\"\n")
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(project))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            assert manager.resolve_user_workspace_path("uploads/chat/ses_a") == (
                home / ".flocks" / "workspace" / "uploads" / "chat" / "ses_a"
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_get_memory_dir_points_to_data_memory(self, tmp_workspace: Path, manager):
        mem = manager.get_memory_dir()
        assert mem.name == "memory"
        # Must be outside workspace
        assert not str(mem).startswith(str(tmp_workspace))

    def test_ensure_dirs_creates_convention_dirs(self, tmp_workspace: Path, manager):
        for name in ["outputs", "knowledge"]:
            assert (tmp_workspace / name).is_dir(), f"Missing convention dir: {name}"

    def test_ensure_dirs_idempotent(self, tmp_workspace: Path, manager):
        """Calling ensure_dirs twice must not raise."""
        manager.ensure_dirs()
        manager.ensure_dirs()

    def test_get_outputs_dir_is_session_scoped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manager
    ):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        out = manager.get_outputs_dir("ses_abc123", day="2026-05-06")
        assert out == home / ".flocks" / "workspace" / "outputs" / "2026-05-06" / "ses_abc123"
        assert out.is_dir()

    def test_get_outputs_dir_sanitizes_session_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manager
    ):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        out = manager.get_outputs_dir("../ses/bad", day="2026-05-06")
        assert out == home / ".flocks" / "workspace" / "outputs" / "2026-05-06" / "ses_bad"
        assert out.is_dir()

    def test_rewrite_legacy_workspace_output_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manager
    ):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        legacy = home / ".flocks" / "workspace" / "outputs" / "2026-05-06" / "report.md"
        rewritten = manager.rewrite_legacy_output_path(legacy, "ses_abc123")
        assert rewritten == (
            home
            / ".flocks"
            / "workspace"
            / "outputs"
            / "2026-05-06"
            / "ses_abc123"
            / "report.md"
        )

    def test_rewrite_project_outputs_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_workspace: Path, manager
    ):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        project = tmp_workspace.parent / "project"
        legacy = project / "outputs" / "report.md"
        rewritten = manager.rewrite_legacy_output_path(legacy, "ses_abc123", source_dir=project)
        assert rewritten == manager.get_outputs_dir("ses_abc123") / "report.md"

    def test_rewrite_sandbox_flocks_outputs_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_workspace: Path, manager
    ):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        legacy = Path("/workspace/.flocks_outputs/2026-05-07/baseline_192.168.185.174.md")
        rewritten = manager.rewrite_legacy_output_path(legacy, "ses_abc123")
        assert rewritten == (
            home
            / ".flocks"
            / "workspace"
            / "outputs"
            / "2026-05-07"
            / "ses_abc123"
            / "baseline_192.168.185.174.md"
        )

    def test_rewrite_dated_project_outputs_to_user_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        deploy_root = tmp_path / "opt" / "zhhtest" / "flocks"
        home = tmp_path / "home"
        deploy_root.mkdir(parents=True)
        home.mkdir()
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(deploy_root))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            legacy = (
                deploy_root
                / "outputs"
                / "2026-05-07"
                / "ses_abc123"
                / "baseline.md"
            )
            rewritten = manager.rewrite_legacy_output_path(legacy, "ses_abc123")
            assert rewritten == (
                home
                / ".flocks"
                / "workspace"
                / "outputs"
                / "2026-05-07"
                / "ses_abc123"
                / "baseline.md"
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    def test_rewrite_project_flocks_workspace_outputs_to_user_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from flocks.config.config import Config
        from flocks.workspace.manager import WorkspaceManager

        deploy_root = tmp_path / "opt" / "zhhtest" / "flocks"
        home = tmp_path / "home"
        deploy_root.mkdir(parents=True)
        home.mkdir()
        monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(deploy_root / ".flocks" / "workspace"))
        monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
        WorkspaceManager._instance = None
        Config._global_config = None
        try:
            manager = WorkspaceManager.get_instance()
            legacy = (
                deploy_root
                / ".flocks"
                / "workspace"
                / "outputs"
                / "2026-05-24"
                / "ses_abc123"
                / "final_report.md"
            )
            rewritten = manager.rewrite_legacy_output_path(legacy, "ses_abc123")
            assert rewritten == (
                home
                / ".flocks"
                / "workspace"
                / "outputs"
                / "2026-05-24"
                / "ses_abc123"
                / "final_report.md"
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None


# ─── Path resolution ──────────────────────────────────────────────────────────

class TestPathResolution:
    def test_resolve_simple_relative(self, tmp_workspace: Path, manager):
        resolved = manager.resolve_workspace_path("outputs/report.pdf")
        assert resolved == tmp_workspace / "outputs" / "report.pdf"

    def test_resolve_nested_relative(self, tmp_workspace: Path, manager):
        resolved = manager.resolve_workspace_path("outputs/abc123/result.json")
        assert resolved == tmp_workspace / "outputs" / "abc123" / "result.json"

    def test_resolve_empty_path_returns_workspace_root(self, manager):
        # Empty string resolves to workspace root — that is allowed
        resolved = manager.resolve_workspace_path("")
        assert resolved == manager.get_workspace_dir().resolve()

    def test_reject_absolute_path(self, manager):
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            manager.resolve_workspace_path("/etc/passwd")

    def test_reject_path_traversal_dotdot(self, manager):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            manager.resolve_workspace_path("../../etc/passwd")

    def test_reject_path_traversal_in_subdir(self, manager):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            manager.resolve_workspace_path("outputs/../../secret")

    def test_memory_resolve_simple(self, tmp_workspace: Path, manager):
        memory_root = manager.get_memory_dir()
        resolved = manager.resolve_memory_path("MEMORY.md")
        assert resolved == (memory_root / "MEMORY.md").resolve()

    def test_memory_reject_absolute(self, manager):
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            manager.resolve_memory_path("/etc/passwd")

    def test_memory_reject_traversal(self, manager):
        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            manager.resolve_memory_path("../../etc/passwd")


# ─── Text-file detection ─────────────────────────────────────────────────────

class TestIsTextFile:
    @pytest.mark.parametrize("filename,expected", [
        ("README.md", True),
        ("notes.txt", True),
        ("server.log", True),
        ("config.json", True),
        ("settings.yaml", True),
        ("settings.yml", True),
        ("pyproject.toml", True),
        ("app.py", True),
        ("index.js", True),
        ("component.ts", True),
        ("component.tsx", True),
        ("deploy.sh", True),
        ("data.csv", True),
        ("report.xml", True),
        ("index.html", True),
        ("style.css", True),
        ("data.sql", True),
        # Binary / non-text
        ("archive.zip", False),
        ("image.png", False),
        ("photo.jpg", False),
        ("document.pdf", False),
        ("binary.exe", False),
        ("disk.dmg", False),
        ("lib.so", False),
        ("data.bin", False),
        # No extension
        ("Makefile", False),
    ])
    def test_extension_detection(self, tmp_path: Path, filename: str, expected: bool):
        from flocks.workspace.manager import WorkspaceManager
        p = tmp_path / filename
        p.touch()
        assert WorkspaceManager.is_text_file(p) == expected
