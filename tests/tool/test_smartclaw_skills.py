"""
Tests for smartclaw/tool/skill/smartclaw_skills.py

Coverage:
- Tool registration in ToolRegistry
- Subcommand allow-list enforcement
- Missing smartclaw executable guard
- Successful execution (mocked subprocess)
- Failed execution (non-zero exit code)
- Timeout handling and proc.kill()
- args whitespace splitting
- Output truncation
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartclaw.tool.registry import ToolRegistry, ToolContext, ToolResult
import smartclaw.tool.skill.smartclaw_skills  # noqa: F401 — ensure module is imported


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx() -> ToolContext:
    ctx = MagicMock(spec=ToolContext)
    ctx.ask = AsyncMock(return_value=None)
    ctx.metadata = MagicMock()
    ctx.aborted = False
    ctx.extra = {}
    return ctx


def make_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


def make_skill(name: str, description: str = "desc", source: str = "project"):
    skill = MagicMock()
    skill.name = name
    skill.description = description
    skill.description_cn = None
    skill.source = source
    skill.requires = None
    return skill


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_tool_is_registered():
    """smartclaw_skills must appear in ToolRegistry after module import."""
    tools = {t.name for t in ToolRegistry.list_tools()}
    assert "smartclaw_skills" in tools


def test_tool_has_expected_parameters():
    tool = next(t for t in ToolRegistry.list_tools() if t.name == "smartclaw_skills")
    param_names = {p.name for p in tool.parameters}
    assert {"subcommand", "args"} == param_names


# ---------------------------------------------------------------------------
# Subcommand allow-list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_subcommand_returns_error():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    result = await smartclaw_skills(ctx, subcommand="hack", args="")
    assert result.success is False
    assert "Unknown subcommand" in (result.error or "")


@pytest.mark.asyncio
async def test_all_allowed_subcommands_accepted():
    """Ensure none of the valid subcommands are rejected by the allow-list."""
    from smartclaw.tool.skill.smartclaw_skills import (
        smartclaw_skills,
        _ALLOWED_SUBCOMMANDS,
        _READ_ONLY_SUBCOMMANDS,
    )

    proc = make_proc(stdout=b"ok", returncode=0)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/local/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc),
    ):
        for sub in _ALLOWED_SUBCOMMANDS:
            ctx = make_ctx()
            result = await smartclaw_skills(ctx, subcommand=sub, args="")
            assert result.success is True, f"subcommand {sub!r} should succeed"
            if sub in _READ_ONLY_SUBCOMMANDS:
                ctx.ask.assert_not_called()
            else:
                ctx.ask.assert_called_once()


# ---------------------------------------------------------------------------
# Missing executable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_smartclaw_executable():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    with patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value=None):
        result = await smartclaw_skills(ctx, subcommand="list")
    assert result.success is False
    assert "not found" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_success():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    proc = make_proc(stdout=b"find-ioc  ndr-alert\n", returncode=0)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
    ):
        result = await smartclaw_skills(ctx, subcommand="list")

    assert result.success is True
    assert "find-ioc" in (result.output or "")
    # Command must include 'skills' and 'list'
    cmd_args = mock_exec.call_args[0]
    assert "skills" in cmd_args
    assert "list" in cmd_args
    # list is read-only — no bash permission prompt
    ctx.ask.assert_not_called()


@pytest.mark.asyncio
async def test_find_passes_args():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    proc = make_proc(stdout=b"phishing analysis skill\n", returncode=0)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
    ):
        result = await smartclaw_skills(ctx, subcommand="find", args="phishing analysis")

    assert result.success is True
    cmd_args = mock_exec.call_args[0]
    assert "find" in cmd_args
    assert "phishing" in cmd_args
    assert "analysis" in cmd_args
    ctx.ask.assert_not_called()


# ---------------------------------------------------------------------------
# Failed execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nonzero_exit_returns_failure():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    proc = make_proc(stderr=b"skill not found\n", returncode=1)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc),
    ):
        result = await smartclaw_skills(ctx, subcommand="install", args="github:bad/source")

    assert result.success is False
    assert "skill not found" in (result.error or "")
    ctx.ask.assert_called_once()


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_context_bypasses_agent_skill_allowlist_for_management():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    ctx.agent = "titan"
    ctx.session_id = "ses_workflow_skills"
    ctx.extra = {"workflow_tool_context": True}
    proc = make_proc(stdout=b"installed\n", returncode=0)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc),
    ):
        result = await smartclaw_skills(ctx, subcommand="install", args="workflow-only-skill")

    assert result.success is True
    ctx.ask.assert_called_once()


@pytest.mark.asyncio
async def test_user_session_list_uses_agent_allowlist_without_cli():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    ctx.agent = "titan"
    ctx.session_id = "ses_user_skills"
    ctx.extra = {}
    skills = [
        make_skill("find-skills", "Find authorized skills"),
        make_skill("onesec-use", "OneSEC platform skill"),
    ]

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.agent.controls.agent_skill_allowlist", new_callable=AsyncMock, return_value=["find-skills"]),
        patch("smartclaw.agent.controls.titan_session_uses_full_skill_catalog", new_callable=AsyncMock, return_value=False),
        patch("smartclaw.skill.skill.Skill.all", new_callable=AsyncMock, return_value=skills),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec") as mock_exec,
    ):
        result = await smartclaw_skills(ctx, subcommand="list")

    assert result.success is True
    assert "find-skills" in (result.output or "")
    assert "onesec-use" not in (result.output or "")
    assert result.metadata.get("agent_scoped") is True
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_user_session_find_searches_only_allowed_installed_skills():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    ctx.agent = "titan"
    ctx.session_id = "ses_user_find_skills"
    ctx.extra = {}
    skills = [
        make_skill("find-skills", "Find authorized skills"),
        make_skill("workflow-builder", "Build workflows"),
        make_skill("qingteng-use", "Qingteng platform skill"),
    ]

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.agent.controls.agent_skill_allowlist", new_callable=AsyncMock, return_value=["find-skills", "workflow-builder"]),
        patch("smartclaw.agent.controls.titan_session_uses_full_skill_catalog", new_callable=AsyncMock, return_value=False),
        patch("smartclaw.skill.skill.Skill.all", new_callable=AsyncMock, return_value=skills),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec") as mock_exec,
    ):
        result = await smartclaw_skills(ctx, subcommand="find", args="platform")

    assert result.success is True
    assert "qingteng-use" not in (result.output or "")
    assert "No allowed installed skills matched" in (result.output or "")
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_context_read_only_still_uses_cli_full_catalog():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    ctx.agent = "titan"
    ctx.session_id = "ses_workflow_list_skills"
    ctx.extra = {"workflow_tool_context": True}
    proc = make_proc(stdout=b"onesec-use\nqingteng-use\n", returncode=0)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.agent.controls.titan_session_uses_full_skill_catalog", new_callable=AsyncMock, return_value=True),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
    ):
        result = await smartclaw_skills(ctx, subcommand="list")

    assert result.success is True
    assert "onesec-use" in (result.output or "")
    mock_exec.assert_called_once()


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_kills_process():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    # communicate() raises TimeoutError on first call (the actual wait_for),
    # then returns empty bytes on the second call (the drain after kill).
    proc = MagicMock()
    proc.kill = MagicMock()
    proc.communicate = AsyncMock(
        side_effect=[asyncio.TimeoutError(), (b"", b"")]
    )
    proc.returncode = None

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc),
    ):
        result = await smartclaw_skills(ctx, subcommand="install", args="clawhub:slow-skill")

    assert result.success is False
    assert "timed out" in (result.error or "").lower()
    proc.kill.assert_called_once()
    ctx.ask.assert_called_once()


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_args_not_appended_to_cmd():
    """Empty args string must not add any extra tokens to the command."""
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    proc = make_proc(stdout=b"", returncode=0)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
    ):
        await smartclaw_skills(ctx, subcommand="status", args="")

    cmd_args = mock_exec.call_args[0]
    # Exactly: smartclaw, skills, status — nothing more
    assert cmd_args == ("/usr/bin/smartclaw", "skills", "status")


@pytest.mark.asyncio
async def test_stderr_merged_into_output():
    """stderr content must appear in the returned output on success."""
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills

    ctx = make_ctx()
    proc = make_proc(stdout=b"stdout line\n", stderr=b"warning: deprecated\n", returncode=0)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc),
    ):
        result = await smartclaw_skills(ctx, subcommand="status")

    assert result.success is True
    assert "stdout line" in (result.output or "")
    assert "warning: deprecated" in (result.output or "")


# ---------------------------------------------------------------------------
# Output truncation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_long_output_is_truncated():
    from smartclaw.tool.skill.smartclaw_skills import smartclaw_skills, _MAX_OUTPUT

    ctx = make_ctx()
    long_output = b"x" * (_MAX_OUTPUT + 500)
    proc = make_proc(stdout=long_output, returncode=0)

    with (
        patch("smartclaw.tool.skill.smartclaw_skills._smartclaw_executable", return_value="/usr/bin/smartclaw"),
        patch("smartclaw.tool.skill.smartclaw_skills.asyncio.create_subprocess_exec", return_value=proc),
    ):
        result = await smartclaw_skills(ctx, subcommand="list")

    assert result.success is True
    assert len(result.output or "") <= _MAX_OUTPUT + 100  # allow for truncation notice
    assert "truncated" in (result.output or "").lower()
