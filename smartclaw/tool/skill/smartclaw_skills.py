"""
smartclaw_skills tool — Skill management for Titan.

Wraps the `smartclaw skills` CLI so Titan can search, install, check status,
and manage agent skills without composing raw bash commands.

Design principle: one tool, all subcommands.  Titan sees the full command
surface in the tool description and picks the right subcommand for each
situation.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from typing import Optional

from smartclaw.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from smartclaw.utils.log import Log


log = Log.create(service="tool.smartclaw_skills")

_TIMEOUT_SEC = 120
_MAX_OUTPUT = 8_000  # chars — keep responses concise for the model

_DESCRIPTION = """\
Manage agent skills: search the registry, install, check dependency status,
install deps, and remove skills.  Use this tool (not bash) for any
`smartclaw skills` operation.

⚠️ IMPORTANT DISTINCTION:
  • To search for skills available in the **external public registry** (not yet installed):
      → use this tool with subcommand="find"
  • To see skills that are **already installed** in the current SmartClaw instance:
      → use run_slash_command(command="skills") instead (not this tool)

## Subcommands

**find <query>**
  Search the **external public skill registry** by keyword.
  This does NOT show installed skills — it discovers skills that can be installed.
  → Use BEFORE telling the user "I can't do X".  A matching skill may exist.
  → To list already-installed skills, use run_slash_command(command="skills") instead.
  Example: smartclaw_skills(subcommand="find", args="malware phishing")

**install <source>**
  Install a skill from an external source.
  Source formats:
    github:<owner>/<repo>/<skill-dir>   e.g. github:octocat/skills/find-ioc
    clawhub:<name>                      e.g. clawhub:ndr-alert-analysis
    https://...                         direct SKILL.md URL
    /local/path or ./relative           local directory
  → After install, always call status to check if deps are missing.
  Example: smartclaw_skills(subcommand="install", args="github:owner/repo/skill-name")

**status**
  Show all discovered skills with eligibility info (missing bins / env vars).
  → Run after install or when the user asks "which skills are ready?".
  Example: smartclaw_skills(subcommand="status")

**install-deps <skill-name>**
  Install the tool dependencies declared in a skill's SKILL.md
  (brew packages, npm globals, uv/pip packages, go binaries).
  → Run when status shows a skill is not eligible.
  Example: smartclaw_skills(subcommand="install-deps", args="find-ioc")

**list**
  List all locally discovered skills with source and description.
  Example: smartclaw_skills(subcommand="list")

**remove <skill-name>**
  Uninstall a user-managed skill from ~/.smartclaw.
  Example: smartclaw_skills(subcommand="remove", args="old-skill")
"""

_DESCRIPTION_CN = """\
管理 agent skills：搜索注册表、安装、检查依赖状态、安装依赖和移除 skills。任何 `smartclaw skills` 操作都应使用此工具，而不是 bash。

重要区分：
  • 搜索外部公共注册表中可用、但尚未安装的 skills：
      → 使用此工具并设置 subcommand="find"
  • 查看当前 SmartClaw 实例中已经安装的 skills：
      → 使用 run_slash_command(command="skills")，不是此工具

## 子命令

**find <query>**
  按关键字搜索外部公共 skill 注册表。
  这不会显示已安装 skills，而是发现可安装的 skills。
  → 在告诉用户“我不能做 X”之前使用，可能存在匹配 skill。
  → 要列出已安装 skills，请使用 run_slash_command(command="skills")。
  示例：smartclaw_skills(subcommand="find", args="malware phishing")

**install <source>**
  从外部源安装 skill。
  source 格式：
    github:<owner>/<repo>/<skill-dir>   例如 github:octocat/skills/find-ioc
    clawhub:<name>                      例如 clawhub:ndr-alert-analysis
    https://...                         直接 SKILL.md URL
    /local/path 或 ./relative           本地目录
  → 安装后务必调用 status 检查是否缺少依赖。
  示例：smartclaw_skills(subcommand="install", args="github:owner/repo/skill-name")

**status**
  显示所有已发现 skills 及其可用性信息（缺失的二进制或环境变量）。
  → 安装后或用户询问“哪些 skills 已就绪？”时运行。
  示例：smartclaw_skills(subcommand="status")

**install-deps <skill-name>**
  安装 skill 的 SKILL.md 中声明的工具依赖（brew 包、npm 全局包、uv/pip 包、go 二进制）。
  → 当 status 显示某个 skill 不可用时运行。
  示例：smartclaw_skills(subcommand="install-deps", args="find-ioc")

**list**
  列出所有本地发现的 skills，包含来源和描述。
  示例：smartclaw_skills(subcommand="list")

**remove <skill-name>**
  从 ~/.smartclaw 卸载用户管理的 skill。
  示例：smartclaw_skills(subcommand="remove", args="old-skill")
"""

# Allowed subcommands — enforced to prevent arbitrary shell injection via args.
# Ordered for consistent display in tool schema enum and error messages.
_ALLOWED_SUBCOMMANDS = frozenset(
    ["find", "install", "status", "install-deps", "list", "remove"]
)
_SUBCOMMAND_ENUM = ["find", "install", "status", "install-deps", "list", "remove"]

# Read-only registry / discovery — no shell side effects; skip bash permission gate.
_READ_ONLY_SUBCOMMANDS = frozenset({"find", "list", "status"})


def _ctx_agent(ctx: ToolContext) -> Optional[str]:
    value = getattr(ctx, "agent", None)
    return value if isinstance(value, str) and value.strip() else None


def _infer_skill_name(args: str) -> str:
    """Best-effort extraction of the target skill name from a CLI arg string."""
    tokens = shlex.split((args or "").strip())
    if not tokens:
        return ""
    source = tokens[0].rstrip("/\\")
    if ":" in source and not source.lower().startswith(("http://", "https://")):
        source = source.rsplit(":", 1)[-1]
    source = source.rstrip("/\\")
    base = os.path.basename(source) or source
    if base.upper() == "SKILL.MD":
        parent = os.path.basename(os.path.dirname(source))
        if parent:
            base = parent
    return base.strip()


def _skill_allowed(skill_name: str, allowed: list[str]) -> bool:
    allowed_names = {str(name).strip().lower() for name in allowed if str(name).strip()}
    return str(skill_name).strip().lower() in allowed_names


def _format_allowed_skill_list(skills: list[object]) -> str:
    if not skills:
        return "No skills are allowed for this agent."

    lines = ["Allowed installed skills for this agent:", ""]
    for skill in sorted(skills, key=lambda item: str(getattr(item, "name", ""))):
        name = getattr(skill, "name", "")
        description = getattr(skill, "description", "") or ""
        source = getattr(skill, "source", None) or "project"
        lines.append(f"- {name}: {description} (source: {source})")
    return "\n".join(lines)


def _format_allowed_skill_status(skills: list[object]) -> str:
    if not skills:
        return "No skills are allowed for this agent."

    lines = ["Allowed installed skill status for this agent:", ""]
    for skill in sorted(skills, key=lambda item: str(getattr(item, "name", ""))):
        name = getattr(skill, "name", "")
        eligible = getattr(skill, "eligible", None)
        missing = getattr(skill, "missing", None) or []
        if eligible is True:
            status = "ready"
        elif eligible is False:
            status = "missing: " + ", ".join(missing)
        else:
            status = "unknown"
        lines.append(f"- {name}: {status}")
    return "\n".join(lines)


def _format_allowed_skill_find(skills: list[object], query: str) -> str:
    query = (query or "").strip()
    if not skills:
        return "No allowed installed skills matched the query."

    heading = (
        f'Allowed installed skills matching "{query}":'
        if query
        else "Allowed installed skills for this agent:"
    )
    lines = [heading, ""]
    for skill in sorted(skills, key=lambda item: str(getattr(item, "name", ""))):
        name = getattr(skill, "name", "")
        description = getattr(skill, "description", "") or ""
        source = getattr(skill, "source", None) or "project"
        lines.append(f"- {name}: {description} (source: {source})")
    return "\n".join(lines)


async def _read_only_allowed_skill_output(subcommand: str, args: str, allowed: list[str]) -> str:
    from smartclaw.skill.skill import Skill

    skills = [
        skill
        for skill in await Skill.all()
        if _skill_allowed(getattr(skill, "name", ""), allowed)
    ]

    if subcommand == "list":
        return _format_allowed_skill_list(skills)

    if subcommand == "status":
        checked = [Skill.check_eligibility(skill) for skill in skills]
        return _format_allowed_skill_status(checked)

    query = (args or "").strip().lower()
    if query:
        skills = [
            skill
            for skill in skills
            if query in str(getattr(skill, "name", "")).lower()
            or query in str(getattr(skill, "description", "") or "").lower()
            or query in str(getattr(skill, "description_cn", "") or "").lower()
        ]
    return _format_allowed_skill_find(skills, args)


def _smartclaw_executable() -> Optional[str]:
    """Locate the `smartclaw` CLI on PATH."""
    return shutil.which("smartclaw")


@ToolRegistry.register_function(
    name="smartclaw_skills",
    description=_DESCRIPTION,
    description_cn=_DESCRIPTION_CN,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="subcommand",
            type=ParameterType.STRING,
            description=(
                "Skill management subcommand: "
                "find | install | status | install-deps | list | remove"
            ),
            required=True,
            enum=_SUBCOMMAND_ENUM,
        ),
        ToolParameter(
            name="args",
            type=ParameterType.STRING,
            description=(
                "Arguments for the subcommand.  "
                "For find: search query.  "
                "For install: source string.  "
                "For install-deps / remove: skill name.  "
                "For status / list: leave empty."
            ),
            required=False,
            default="",
        ),
    ],
)
async def smartclaw_skills(
    ctx: ToolContext,
    subcommand: str,
    args: str = "",
) -> ToolResult:
    """Execute a `smartclaw skills <subcommand>` command and return its output."""
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        return ToolResult(
            success=False,
            error=(
                f"Unknown subcommand: {subcommand!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_SUBCOMMANDS))}"
            ),
        )

    smartclaw_bin = _smartclaw_executable()
    if smartclaw_bin is None:
        return ToolResult(
            success=False,
            error=(
                "The `smartclaw` CLI was not found on PATH. "
                "Make sure SmartClaw is installed and activated in the current environment."
            ),
        )

    # Build the command list — no shell interpolation, safe from injection.
    from smartclaw.agent.controls import (
        agent_allows_skill,
        agent_skill_allowlist,
        titan_session_uses_full_skill_catalog,
    )

    agent_name = _ctx_agent(ctx)
    if await titan_session_uses_full_skill_catalog(
        getattr(ctx, "session_id", None),
        agent_name,
        getattr(ctx, "extra", None),
    ):
        skill_allowlist = None
    else:
        skill_allowlist = await agent_skill_allowlist(agent_name)
    if skill_allowlist is not None:
        if subcommand in {"install", "install-deps", "remove"}:
            target_skill = _infer_skill_name(args)
            if not target_skill or not await agent_allows_skill(agent_name, target_skill):
                allowed_text = ", ".join(skill_allowlist) or "none"
                return ToolResult(
                    success=False,
                    error=(
                        f'Agent "{agent_name or ""}" is not allowed to manage skill '
                        f'"{target_skill or args}". Allowed skills: {allowed_text}'
                    ),
                )
        elif subcommand in _READ_ONLY_SUBCOMMANDS and not skill_allowlist:
            return ToolResult(
                success=True,
                output="No skills are allowed for this agent.",
                title=f"smartclaw skills {subcommand}",
            )
        elif subcommand in _READ_ONLY_SUBCOMMANDS:
            output = await _read_only_allowed_skill_output(subcommand, args, skill_allowlist)
            return ToolResult(
                success=True,
                output=output,
                title=f"smartclaw skills {subcommand}",
                metadata={
                    "agent_scoped": True,
                    "agent": agent_name,
                    "allowed_skills": list(skill_allowlist),
                },
            )

    cmd: list[str] = [smartclaw_bin, "skills", subcommand]
    if args.strip():
        # shlex.split preserves quoted tokens (e.g. paths with spaces).
        cmd += shlex.split(args.strip())

    log.info("smartclaw_skills.run", {"cmd": cmd})

    # Mutating subcommands need bash approval. Read-only (find/list/status) runs
    # without prompting — same trust model as listing skills in the UI.
    #
    # For install/remove/install-deps, always-patterns must match the *full*
    # argv string (e.g. "/opt/smartclaw/bin/smartclaw skills install ..."); a bare
    # "smartclaw skills *" fails fnmatch and never auto-approved.
    if subcommand not in _READ_ONLY_SUBCOMMANDS:
        await ctx.ask(
            permission="bash",
            patterns=[" ".join(cmd)],
            always=["*smartclaw skills *"],
            metadata={"subcommand": subcommand},
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            # Drain pipes so the process can exit cleanly and avoid zombies.
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                pass
            return ToolResult(
                success=False,
                error=f"Command timed out after {_TIMEOUT_SEC}s: {' '.join(cmd)}",
            )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Failed to start smartclaw CLI: {exc}",
        )

    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")
    output = (stdout + stderr).strip()

    # Truncate very long output so we don't flood the context window.
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + f"\n\n[… output truncated at {_MAX_OUTPUT} chars]"

    exit_code = proc.returncode
    success = exit_code == 0

    if success:
        return ToolResult(
            success=True,
            output=output or f"smartclaw skills {subcommand}: completed (no output)",
            title=f"smartclaw skills {subcommand}",
        )

    return ToolResult(
        success=False,
        error=output or f"smartclaw skills {subcommand} failed (exit {exit_code})",
        title=f"smartclaw skills {subcommand}",
    )
