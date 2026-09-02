"""Tests for bash tool error propagation."""

from pathlib import Path
from datetime import date

import pytest

from smartclaw.tool.code.bash import (
    _bash_output_env,
    _is_smartclaw_plugin_write_path,
    _is_user_smartclaw_plugin_write_path,
    _migrate_misrouted_project_workspace_outputs,
    _migrate_nested_session_outputs,
    _stream_output,
)
from smartclaw.tool.code import bash as bash_module
from smartclaw.tool.code.bash_blacklist import find_blacklisted_command
from smartclaw.tool.code.shell_risk import classify_shell_risk
from smartclaw.config.config import ConfigInfo
from smartclaw.tool.registry import ToolContext


def test_bash_description_marks_ops_changes_high_risk() -> None:
    description = bash_module.get_description("/workspace")

    assert "High-risk operations" in description
    assert "stopping services" in description
    assert "closing or blocking ports" in description
    assert "capability gap" in description


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

    assert _is_smartclaw_plugin_write_path(
        ".smartclaw/plugins/tools/api/demo.yaml",
        str(project),
    )
    assert _is_smartclaw_plugin_write_path(
        "/workspace/.smartclaw/plugins/agents/demo/agent.yaml",
        str(project),
    )
    assert not _is_smartclaw_plugin_write_path("outputs/report.md", str(project))


def test_user_plugin_shell_write_path_is_blocked_from_project_allowlist(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    user_plugin = Path.home() / ".smartclaw" / "plugins" / "tools" / "api" / "demo.yaml"

    assert _is_user_smartclaw_plugin_write_path(str(user_plugin))
    assert not _is_smartclaw_plugin_write_path(str(user_plugin), str(project))


def test_bash_output_env_points_to_user_workspace(tmp_path: Path, monkeypatch) -> None:
    from smartclaw.config.config import Config
    from smartclaw.workspace.manager import WorkspaceManager

    home = tmp_path / "home"
    project = tmp_path / "opt" / "zhhtest" / "smartclaw"
    home.mkdir()
    project.mkdir(parents=True)
    monkeypatch.setenv("SMARTCLAW_WORKSPACE_DIR", str(project / ".smartclaw" / "workspace"))
    monkeypatch.setattr("smartclaw.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        ctx = ToolContext(session_id="ses_abc123", message_id="m-bash")
        env = _bash_output_env(ctx)
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    assert env["SMARTCLAW_WORKSPACE_DIR"] == str(home / ".smartclaw" / "workspace")
    assert env["SMARTCLAW_OUTPUTS_DIR"] == str(
        home / ".smartclaw" / "workspace" / "outputs" / date.today().isoformat() / "ses_abc123"
    )


def test_migrate_misrouted_project_workspace_outputs(tmp_path: Path, monkeypatch) -> None:
    from smartclaw.config.config import Config
    from smartclaw.workspace.manager import WorkspaceManager

    home = tmp_path / "home"
    project = tmp_path / "opt" / "zhhtest" / "smartclaw"
    wrong_dir = project / ".smartclaw" / "workspace" / "outputs" / "2026-05-24" / "ses_abc123"
    wrong_file = wrong_dir / "final_report.md"
    home.mkdir()
    wrong_dir.mkdir(parents=True)
    wrong_file.write_text("report", encoding="utf-8")
    monkeypatch.setenv("SMARTCLAW_WORKSPACE_DIR", str(project / ".smartclaw" / "workspace"))
    monkeypatch.setattr("smartclaw.workspace.manager._user_home_dir", lambda: home)
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
        / ".smartclaw"
        / "workspace"
        / "outputs"
        / "2026-05-24"
        / "ses_abc123"
        / "final_report.md"
    )
    assert migrated == [{"from": str(wrong_file), "to": str(expected)}]
    assert expected.read_text(encoding="utf-8") == "report"
    assert not wrong_file.exists()


def test_migrate_nested_session_outputs(tmp_path: Path, monkeypatch) -> None:
    from smartclaw.config.config import Config
    from smartclaw.workspace.manager import WorkspaceManager

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("smartclaw.workspace.manager._user_home_dir", lambda: home)
    WorkspaceManager._instance = None
    Config._global_config = None
    try:
        session_id = "ses_abc123"
        today = date.today().isoformat()
        output_dir = WorkspaceManager.get_instance().get_outputs_dir(
            session_id,
            day=today,
        )
        wrong_dir = output_dir / today / session_id
        wrong_file = wrong_dir / "final_report.md"
        wrong_dir.mkdir(parents=True)
        wrong_file.write_text("report", encoding="utf-8")

        ctx = ToolContext(session_id=session_id, message_id="m-bash")
        migrated = _migrate_nested_session_outputs(ctx)
    finally:
        WorkspaceManager._instance = None
        Config._global_config = None

    expected = output_dir / "final_report.md"
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


def test_shell_risk_classifier_detects_service_stop_scripts() -> None:
    direct = classify_shell_risk("/opt/smartgpt103/smartclaw/stop.sh")
    chained = classify_shell_risk("cd /opt/smartgpt103/smartclaw && ./stop.sh")
    interpreted = classify_shell_risk("bash /opt/smartgpt103/smartclaw/restart.sh")

    assert direct is not None
    assert direct.risk_type == "service_stop"
    assert chained is not None
    assert chained.risk_type == "service_stop"
    assert interpreted is not None
    assert interpreted.risk_type == "service_restart"


def test_shell_risk_classifier_detects_ops_control_commands() -> None:
    assert classify_shell_risk("sudo systemctl stop nginx").risk_type == "service_stop"
    assert classify_shell_risk("kill -9 1234").risk_type == "process_control"
    assert classify_shell_risk("docker restart api").risk_type == "container_restart"
    assert classify_shell_risk("iptables -A INPUT -p tcp --dport 8080 -j DROP").risk_type == "firewall_change"


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

    monkeypatch.setattr("smartclaw.config.config.Config.get", fake_config_get)
    monkeypatch.setattr(bash_module.Instance, "get_directory", lambda: str(Path.cwd()))
    monkeypatch.setattr(bash_module, "_get_sandbox_config_from_ctx", lambda _ctx: None)
    monkeypatch.setattr(bash_module, "_execute_host", fake_execute_host)

    result = await bash_module.bash_tool(ctx=ctx, command="echo ok && rm -rf /tmp/demo")

    assert result.success is False
    assert result.error == "没有执行，黑名单命令已被拒绝：rm"
    assert result.metadata["blocked_by_bash_blacklist"] is True
    assert result.metadata["blocked_command"] == "rm"
    assert requests == []


@pytest.mark.asyncio
async def test_bash_tool_blocks_high_risk_command_before_execution(monkeypatch) -> None:
    requests = []
    process_started = False

    async def permission_callback(request):
        requests.append(request)

    async def fake_create_subprocess_shell(*_args, **_kwargs):
        nonlocal process_started
        process_started = True
        raise AssertionError("high-risk command should not start a process")

    ctx = ToolContext(
        session_id="s-bash",
        message_id="m-bash",
        permission_callback=permission_callback,
    )

    monkeypatch.setattr(bash_module.Instance, "contains_path", lambda _path: True)
    monkeypatch.setattr(bash_module.sys, "platform", "linux")
    monkeypatch.setattr(bash_module.asyncio, "create_subprocess_shell", fake_create_subprocess_shell)

    result = await bash_module._execute_host(
        ctx=ctx,
        command="/opt/smartgpt103/smartclaw/stop.sh",
        cwd=str(Path.cwd()),
        timeout_sec=1,
        timeout_ms=1000,
        description="Stop service",
    )

    assert result.success is False
    assert "出于安全考虑" in result.error
    assert "不能直接执行" in result.error
    assert result.metadata["blocked_by_high_risk_shell"] is True
    assert result.metadata["risk_level"] == "high"
    assert result.metadata["command"] == "/opt/smartgpt103/smartclaw/stop.sh"
    assert requests == []
    assert process_started is False
