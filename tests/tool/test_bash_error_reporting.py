"""Tests for bash tool error propagation."""

from pathlib import Path
from datetime import date

import pytest

from flocks.tool.code.bash import (
    _bash_output_env,
    _is_flocks_plugin_write_path,
    _is_user_flocks_plugin_write_path,
    _migrate_misrouted_project_workspace_outputs,
    _stream_output,
)
from flocks.tool.code import bash as bash_module
from flocks.tool.code.bash_blacklist import find_blacklisted_command
from flocks.config.config import ConfigInfo
from flocks.tool.registry import ToolContext


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _size: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProcess:
    def __init__(self, *, stdout_chunks, stderr_chunks, returncode):
        self.stdout = _FakeStream(stdout_chunks)
        self.stderr = _FakeStream(stderr_chunks)
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_stream_output_sets_error_from_captured_stderr() -> None:
    ctx = ToolContext(session_id="s-bash", message_id="m-bash")
    proc = _FakeProcess(
        stdout_chunks=[b""],
        stderr_chunks=[b"Navigation failed: net::ERR_CERT_AUTHORITY_INVALID\n"],
        returncode=1,
    )

    result = await _stream_output(
        ctx=ctx,
        proc=proc,
        command="agent-browser open https://example.com",
        timeout_sec=1,
        timeout_ms=1000,
        description="Open example",
    )

    assert result.success is False
    assert "Command failed with exit code 1" in result.error
    assert "ERR_CERT_AUTHORITY_INVALID" in result.error
    assert "ERR_CERT_AUTHORITY_INVALID" in result.output
    assert result.metadata["exit"] == 1


def test_plugin_yaml_shell_write_path_is_not_treated_as_generated_output(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert _is_flocks_plugin_write_path(
        ".flocks/plugins/tools/api/demo.yaml",
        str(project),
    )
    assert _is_flocks_plugin_write_path(
        "/workspace/.flocks/plugins/agents/demo/agent.yaml",
        str(project),
    )
    assert not _is_flocks_plugin_write_path("outputs/report.md", str(project))


def test_user_plugin_shell_write_path_is_blocked_from_project_allowlist(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    user_plugin = Path.home() / ".flocks" / "plugins" / "tools" / "api" / "demo.yaml"

    assert _is_user_flocks_plugin_write_path(str(user_plugin))
    assert not _is_flocks_plugin_write_path(str(user_plugin), str(project))


def test_bash_output_env_points_to_user_workspace(tmp_path: Path, monkeypatch) -> None:
    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    home = tmp_path / "home"
    project = tmp_path / "opt" / "zhhtest" / "flocks"
    home.mkdir()
    project.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(project / ".flocks" / "workspace"))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        ctx = ToolContext(session_id="ses_abc123", message_id="m-bash")
        env = _bash_output_env(ctx)
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    assert env["FLOCKS_WORKSPACE_DIR"] == str(home / ".flocks" / "workspace")
    assert env["FLOCKS_OUTPUTS_DIR"] == str(
        home / ".flocks" / "workspace" / "outputs" / date.today().isoformat() / "ses_abc123"
    )


def test_migrate_misrouted_project_workspace_outputs(tmp_path: Path, monkeypatch) -> None:
    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    home = tmp_path / "home"
    project = tmp_path / "opt" / "zhhtest" / "flocks"
    wrong_dir = project / ".flocks" / "workspace" / "outputs" / "2026-05-24" / "ses_abc123"
    wrong_file = wrong_dir / "final_report.md"
    home.mkdir()
    wrong_dir.mkdir(parents=True)
    wrong_file.write_text("report", encoding="utf-8")
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(project / ".flocks" / "workspace"))
    monkeypatch.setattr("flocks.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        ctx = ToolContext(session_id="ses_abc123", message_id="m-bash")
        migrated = _migrate_misrouted_project_workspace_outputs(ctx, str(project))
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    expected = (
        home
        / ".flocks"
        / "workspace"
        / "outputs"
        / "2026-05-24"
        / "ses_abc123"
        / "final_report.md"
    )
    assert migrated == [{"from": str(wrong_file), "to": str(expected)}]
    assert expected.read_text(encoding="utf-8") == "report"
    assert not wrong_file.exists()


def test_bash_blacklist_matches_command_names_without_text_false_positives() -> None:
    rules = ["rm", "sudo", "shutdown", "reboot"]

    assert find_blacklisted_command("rm -rf /tmp/demo", rules) == ("rm", "rm")
    assert find_blacklisted_command("echo ok && /usr/bin/rm -rf /tmp/demo", rules) == ("rm", "rm")
    assert find_blacklisted_command("echo $(rm -rf /tmp/demo)", rules) == ("rm", "rm")
    assert find_blacklisted_command("echo rm", rules) is None
    assert find_blacklisted_command("cat remove.txt", rules) is None


@pytest.mark.asyncio
async def test_bash_tool_rejects_configured_blacklisted_command_before_execution(monkeypatch) -> None:
    requests = []

    async def permission_callback(request):
        requests.append(request)

    async def fake_config_get():
        return ConfigInfo(
            bash={
                "command_black_list": ["rm"],
                "block_message": "没有执行，黑名单命令已被拒绝：{command}",
            }
        )

    async def fake_execute_host(**_kwargs):
        raise AssertionError("_execute_host should not be called for blacklisted commands")

    ctx = ToolContext(
        session_id="s-bash",
        message_id="m-bash",
        permission_callback=permission_callback,
    )

    monkeypatch.setattr("flocks.config.config.Config.get", fake_config_get)
    monkeypatch.setattr(bash_module.Instance, "get_directory", lambda: str(Path.cwd()))
    monkeypatch.setattr(bash_module, "_get_sandbox_config_from_ctx", lambda _ctx: None)
    monkeypatch.setattr(bash_module, "_execute_host", fake_execute_host)

    result = await bash_module.bash_tool(ctx=ctx, command="echo ok && rm -rf /tmp/demo")

    assert result.success is False
    assert result.error == "没有执行，黑名单命令已被拒绝：rm"
    assert result.metadata["blocked_by_bash_blacklist"] is True
    assert result.metadata["blocked_command"] == "rm"
    assert requests == []
