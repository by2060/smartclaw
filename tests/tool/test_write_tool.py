"""
Tests for Write tool

Verifies file writing behavior:
- Absolute paths are written directly (no path modification)
- Relative paths fall back to Instance directory
- Sandbox read-only mode blocks writes
- Non-string content is coerced
"""

import os
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from flocks.tool.registry import ToolRegistry, ToolContext


def _make_ctx(**extra_kwargs) -> ToolContext:
    """Create a minimal ToolContext that auto-approves all permissions."""

    async def _auto_approve(req):
        pass

    params = {
        "session_id": "test-session",
        "message_id": "msg-1",
        "agent": "test",
        "call_id": "call-1",
        "permission_callback": _auto_approve,
    }
    params.update(extra_kwargs)
    return ToolContext(**params)


@pytest.mark.asyncio
async def test_absolute_path_written_directly(tmp_path):
    """Absolute filePath must be written to the exact location given."""
    target = tmp_path / "exact_location.txt"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(target), content="absolute"
    )

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "absolute"


@pytest.mark.asyncio
async def test_write_to_workspace_outputs_no_modification(tmp_path):
    """Write to workspace outputs path — tool must not alter the path."""
    outputs_dir = tmp_path / "outputs" / "2026-03-14"
    outputs_dir.mkdir(parents=True)
    target = outputs_dir / "hello.py"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(target), content='print("hi")'
    )

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == 'print("hi")'


@pytest.mark.asyncio
async def test_legacy_workspace_outputs_path_rewritten_to_session(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        legacy_target = home / ".flocks" / "workspace" / "outputs" / "2026-05-06" / "baseline.md"
        expected = (
            home
            / ".flocks"
            / "workspace"
            / "outputs"
            / "2026-05-06"
            / "test-session"
            / "baseline.md"
        )

        ctx = _make_ctx()
        result = await ToolRegistry.execute(
            "write", ctx, filePath=str(legacy_target), content="session scoped"
        )
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert not legacy_target.exists()
    assert expected.exists()
    assert expected.read_text() == "session scoped"
    assert result.metadata["filepath"] == str(expected)
    assert result.metadata["rewritten_from"] == str(legacy_target)


@pytest.mark.asyncio
async def test_nested_workspace_outputs_path_not_rewritten(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        target = (
            home
            / ".flocks"
            / "workspace"
            / "outputs"
            / "2026-05-06"
            / "test-session"
            / "report.md"
        )

        ctx = _make_ctx()
        result = await ToolRegistry.execute(
            "write", ctx, filePath=str(target), content="already scoped"
        )
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "already scoped"
    assert result.metadata["rewritten_from"] is None


@pytest.mark.asyncio
async def test_write_uses_output_session_id_from_context(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        legacy_target = home / ".flocks" / "workspace" / "outputs" / "2026-05-06" / "child-session" / "report.md"
        expected = (
            home
            / ".flocks"
            / "workspace"
            / "outputs"
            / "2026-05-06"
            / "root-session"
            / "child-session"
            / "report.md"
        )

        ctx = _make_ctx(
            session_id="child-session",
            extra={"output_session_id": "root-session"},
        )
        result = await ToolRegistry.execute(
            "write", ctx, filePath=str(legacy_target), content="root scoped"
        )
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert expected.exists()
    assert expected.read_text() == "root scoped"
    assert result.metadata["filepath"] == str(expected)


@pytest.mark.asyncio
async def test_project_outputs_path_rewritten_to_workspace_session(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.project.instance import Instance
    from flocks.workspace.manager import WorkspaceManager

    project_dir = tmp_path / "project"
    workspace = project_dir / ".flocks" / "workspace"
    home = tmp_path / "home"
    project_dir.mkdir()
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    with patch.object(Instance, "get_directory", return_value=str(project_dir)):
        try:
            legacy_target = project_dir / "outputs" / "baseline_check_192.168.185.175.md"
            expected = (
                home
                / ".flocks"
                / "workspace"
                / "outputs"
                / date.today().isoformat()
                / "test-session"
                / "baseline_check_192.168.185.175.md"
            )

            ctx = _make_ctx()
            result = await ToolRegistry.execute(
                "write", ctx, filePath=str(legacy_target), content="project output"
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert not legacy_target.exists()
    assert expected.exists()
    assert expected.read_text() == "project output"
    assert result.metadata["filepath"] == str(expected)
    assert result.metadata["rewritten_from"] == str(legacy_target)


@pytest.mark.asyncio
async def test_relative_outputs_path_rewritten_to_workspace_session(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.project.instance import Instance
    from flocks.workspace.manager import WorkspaceManager

    project_dir = tmp_path / "project"
    workspace = project_dir / ".flocks" / "workspace"
    home = tmp_path / "home"
    project_dir.mkdir()
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    with patch.object(Instance, "get_directory", return_value=str(project_dir)):
        try:
            expected = (
                home
                / ".flocks"
                / "workspace"
                / "outputs"
                / date.today().isoformat()
                / "test-session"
                / "baseline_check_192.168.185.175.md"
            )

            ctx = _make_ctx()
            result = await ToolRegistry.execute(
                "write",
                ctx,
                filePath="outputs/baseline_check_192.168.185.175.md",
                content="relative output",
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert expected.exists()
    assert expected.read_text() == "relative output"
    assert result.metadata["filepath"] == str(expected)


@pytest.mark.asyncio
async def test_user_plugin_agent_files_are_rewritten_to_project_plugins(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.project.instance import Instance
    from flocks.workspace.manager import WorkspaceManager

    project_dir = tmp_path / "project"
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    project_dir.mkdir()
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    with patch.object(Instance, "get_directory", return_value=str(project_dir)):
        try:
            target = home / ".flocks" / "plugins" / "agents" / "demo-agent" / "agent.yaml"
            expected = project_dir / ".flocks" / "plugins" / "agents" / "demo-agent" / "agent.yaml"
            wrong_output = (
                home
                / ".flocks"
                / "workspace"
                / "outputs"
                / date.today().isoformat()
                / "test-session"
                / "agent.yaml"
            )

            ctx = _make_ctx()
            result = await ToolRegistry.execute(
                "write",
                ctx,
                filePath=str(target),
                content="name: demo-agent\nmode: subagent\n",
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert not target.exists()
    assert expected.exists()
    assert expected.read_text() == "name: demo-agent\nmode: subagent\n"
    assert not wrong_output.exists()
    assert result.metadata["filepath"] == str(expected)
    assert result.metadata["rewritten_from"] == str(target)


@pytest.mark.asyncio
async def test_project_plugin_tool_files_are_not_rewritten_to_outputs(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.project.instance import Instance
    from flocks.workspace.manager import WorkspaceManager

    project_dir = tmp_path / "project"
    workspace = project_dir / ".flocks" / "workspace"
    home = tmp_path / "home"
    project_dir.mkdir()
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    with patch.object(Instance, "get_directory", return_value=str(project_dir)):
        try:
            target = project_dir / ".flocks" / "plugins" / "tools" / "api" / "demo.yaml"
            wrong_output = (
                home
                / ".flocks"
                / "workspace"
                / "outputs"
                / date.today().isoformat()
                / "test-session"
                / "demo.yaml"
            )

            ctx = _make_ctx()
            result = await ToolRegistry.execute(
                "write",
                ctx,
                filePath=".flocks/plugins/tools/api/demo.yaml",
                content="name: demo\n",
            )
        finally:
            WorkspaceManager._instance = None
            Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "name: demo\n"
    assert not wrong_output.exists()
    assert result.metadata["filepath"] == str(target)
    assert result.metadata["rewritten_from"] is None


@pytest.mark.asyncio
async def test_sandbox_flocks_outputs_alias_rewritten_to_workspace_outputs(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        legacy_target = "/workspace/.flocks_outputs/2026-05-07/baseline_192.168.185.174.md"
        expected = (
            home
            / ".flocks"
            / "workspace"
            / "outputs"
            / "2026-05-07"
            / "test-session"
            / "baseline_192.168.185.174.md"
        )
        sandbox_root = tmp_path / "sandbox"
        sandbox_root.mkdir()
        sandbox = {
            "workspace_dir": str(sandbox_root),
            "workspace_access": "rw",
        }

        ctx = _make_ctx(extra={"sandbox": sandbox})
        result = await ToolRegistry.execute(
            "write", ctx, filePath=legacy_target, content="sandbox alias"
        )
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert expected.exists()
    assert expected.read_text() == "sandbox alias"
    assert result.metadata["filepath"] == str(expected)
    assert result.metadata["rewritten_from"].replace("\\", "/").endswith(
        "/workspace/.flocks_outputs/2026-05-07/baseline_192.168.185.174.md"
    )


@pytest.mark.asyncio
async def test_sandbox_user_workspace_outputs_path_not_rewritten_to_project_workspace(
    tmp_path, monkeypatch
):
    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    home = tmp_path / "home"
    project_dir = tmp_path / "project"
    project_workspace = project_dir / ".flocks" / "workspace"
    home.mkdir()
    project_dir.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(project_workspace))
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        target = (
            home
            / ".flocks"
            / "workspace"
            / "outputs"
            / "2026-05-07"
            / "test-session"
            / "baseline_report_192.168.185.174.md"
        )
        wrong_project_target = (
            project_workspace
            / "outputs"
            / "2026-05-07"
            / "test-session"
            / "baseline_report_192.168.185.174.md"
        )
        sandbox = {
            "workspace_dir": str(project_dir),
            "workspace_access": "rw",
        }

        ctx = _make_ctx(extra={"sandbox": sandbox})
        with patch("flocks.workspace.manager._user_home_dir", return_value=home):
            result = await ToolRegistry.execute(
                "write", ctx, filePath=str(target), content="user workspace output"
            )
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "user workspace output"
    assert not wrong_project_target.exists()
    assert result.metadata["filepath"] == str(target)
    assert result.metadata["rewritten_from"] is None


@pytest.mark.asyncio
async def test_dated_project_root_outputs_path_rewritten_to_user_workspace(
    tmp_path, monkeypatch
):
    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    deploy_root = tmp_path / "opt" / "zhhtest" / "flocks"
    home = tmp_path / "home"
    deploy_root.mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(deploy_root))
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        legacy_target = (
            deploy_root
            / "outputs"
            / "2026-05-07"
            / "test-session"
            / "baseline_report_192.168.185.174.md"
        )
        expected = (
            home
            / ".flocks"
            / "workspace"
            / "outputs"
            / "2026-05-07"
            / "test-session"
            / "baseline_report_192.168.185.174.md"
        )

        ctx = _make_ctx()
        with patch("flocks.workspace.manager._user_home_dir", return_value=home):
            result = await ToolRegistry.execute(
                "write", ctx, filePath=str(legacy_target), content="project root output"
            )
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    assert result.success, f"write failed: {result.error}"
    assert expected.exists()
    assert expected.read_text() == "project root output"
    assert not legacy_target.exists()
    assert result.metadata["filepath"] == str(expected)
    assert result.metadata["rewritten_from"] == str(legacy_target)


@pytest.mark.asyncio
async def test_sandbox_readonly_blocks_write(tmp_path):
    """Write must fail when sandbox.workspace_access == 'ro'."""
    sandbox = {
        "workspace_dir": str(tmp_path),
        "workspace_access": "ro",
    }
    ctx = _make_ctx(extra={"sandbox": sandbox})

    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(tmp_path / "blocked.txt"), content="x"
    )

    assert not result.success
    assert "read-only" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_dict_content_serialized_to_json(tmp_path):
    """Dict content should be serialized as pretty-printed JSON."""
    target = tmp_path / "data.json"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(target), content={"key": "value", "num": 42}
    )

    assert result.success, f"write failed: {result.error}"
    import json

    data = json.loads(target.read_text())
    assert data == {"key": "value", "num": 42}


@pytest.mark.asyncio
async def test_creates_parent_directory(tmp_path):
    """Write should auto-create parent directories if they don't exist."""
    target = tmp_path / "deep" / "nested" / "file.txt"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(target), content="nested"
    )

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "nested"


def test_filepath_parameter_references_env():
    """filePath parameter description must contain directory routing rules."""
    from flocks.tool.registry import ToolRegistry

    tool = ToolRegistry.get("write")
    filepath_param = next(p for p in tool.info.parameters if p.name == "filePath")
    desc = filepath_param.description

    assert "Workspace outputs directory" in desc
    assert "<env>" in desc
    assert "Source code directory" in desc
