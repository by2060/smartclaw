"""
flocks_skills tool — Skill management for Rex.

Wraps the `flocks skills` CLI so Rex can search, install, check status,
and manage agent skills without composing raw bash commands.

Design principle: one tool, all subcommands.  Rex sees the full command
surface in the tool description and picks the right subcommand for each
situation.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from typing import Optional

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log


log = Log.create(service="tool.flocks_skills")

_TIMEOUT_SEC = 120
_MAX_OUTPUT = 8_000  # chars — keep responses concise for the model

_DESCRIPTION = """\
Manage agent skills: search the registry, install, check dependency status,
install deps, and remove skills.  Use this tool (not bash) for any
`flocks skills` operation.

⚠️ IMPORTANT DISTINCTION:
  • To search for skills available in the **external public registry** (not yet installed):
      → use this tool with subcommand="find"
  • To see skills that are **already installed** in the current Flocks instance:
      → use run_slash_command(command="skills") instead (not this tool)

## Subcommands

**find <query>**
  Search the **external public skill registry** by keyword.
  This does NOT show installed skills — it discovers skills that can be installed.
  → Use BEFORE telling the user "I can't do X".  A matching skill may exist.
  → To list already-installed skills, use run_slash_command(command="skills") instead.
  Example: flocks_skills(subcommand="find", args="malware phishing")

**install <source>**
  Install a skill from an external source.
  Source formats:
    github:<owner>/<repo>/<skill-dir>   e.g. github:octocat/skills/find-ioc
    clawhub:<name>                      e.g. clawhub:ndr-alert-analysis
    https://...                         direct SKILL.md URL
    /local/path or ./relative           local directory
  → After install, always call status to check if deps are missing.
  Example: flocks_skills(subcommand="install", args="github:owner/repo/skill-name")

**status**
  Show all discovered skills with eligibility info (missing bins / env vars).
  → Run after install or when the user asks "which skills are ready?".
  Example: flocks_skills(subcommand="status")

**install-deps <skill-name>**
  Install the tool dependencies declared in a skill's SKILL.md
  (brew packages, npm globals, uv/pip packages, go binaries).
  → Run when status shows a skill is not eligible.
  Example: flocks_skills(subcommand="install-deps", args="find-ioc")

**list**
  List all locally discovered skills with source and description.
  Example: flocks_skills(subcommand="list")

**remove <skill-name>**
  Uninstall a user-managed skill from ~/.flocks.
  Example: flocks_skills(subcommand="remove", args="old-skill")
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


def _filter_text_output_by_skills(output: str, allowed: list[str]) -> str:
    if not allowed:
        return "No skills are allowed for this agent."

    allowed_lower = [name.lower() for name in allowed]
    kept: list[str] = []
    for line in (output or "").splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped:
            kept.append(line)
        elif any(name in lower for name in allowed_lower):
            kept.append(line)
        elif set(stripped) <= {"-", "=", " "}:
            kept.append(line)
        elif lower.startswith(("skill", "name", "available", "status", "eligible", "source")):
            kept.append(line)

    filtered = "\n".join(kept).strip()
    return filtered or "No allowed skills matched the command output."


def _flocks_executable() -> Optional[str]:
    """Locate the `flocks` CLI on PATH."""
    return shutil.which("flocks")


@ToolRegistry.register_function(
    name="flocks_skills",
    description=_DESCRIPTION,
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
async def flocks_skills(
    ctx: ToolContext,
    subcommand: str,
    args: str = "",
) -> ToolResult:
    """Execute a `flocks skills <subcommand>` command and return its output."""
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        return ToolResult(
            success=False,
            error=(
                f"Unknown subcommand: {subcommand!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_SUBCOMMANDS))}"
            ),
        )

    flocks_bin = _flocks_executable()
    if flocks_bin is None:
        return ToolResult(
            success=False,
            error=(
                "The `flocks` CLI was not found on PATH. "
                "Make sure Flocks is installed and activated in the current environment."
            ),
        )

    # Build the command list — no shell interpolation, safe from injection.
    from flocks.agent.controls import agent_allows_skill, agent_skill_allowlist

    agent_name = _ctx_agent(ctx)
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
                title=f"flocks skills {subcommand}",
            )

    cmd: list[str] = [flocks_bin, "skills", subcommand]
    if args.strip():
        # shlex.split preserves quoted tokens (e.g. paths with spaces).
        cmd += shlex.split(args.strip())

    log.info("flocks_skills.run", {"cmd": cmd})

    # Mutating subcommands need bash approval. Read-only (find/list/status) runs
    # without prompting — same trust model as listing skills in the UI.
    #
    # For install/remove/install-deps, always-patterns must match the *full*
    # argv string (e.g. "/opt/flocks/bin/flocks skills install ..."); a bare
    # "flocks skills *" fails fnmatch and never auto-approved.
    if subcommand not in _READ_ONLY_SUBCOMMANDS:
        await ctx.ask(
            permission="bash",
            patterns=[" ".join(cmd)],
            always=["*flocks skills *"],
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
            error=f"Failed to start flocks CLI: {exc}",
        )

    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")
    output = (stdout + stderr).strip()

    # Truncate very long output so we don't flood the context window.
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + f"\n\n[… output truncated at {_MAX_OUTPUT} chars]"

    if subcommand in _READ_ONLY_SUBCOMMANDS and skill_allowlist is not None:
        output = _filter_text_output_by_skills(output, skill_allowlist)

    exit_code = proc.returncode
    success = exit_code == 0

    if success:
        return ToolResult(
            success=True,
            output=output or f"flocks skills {subcommand}: completed (no output)",
            title=f"flocks skills {subcommand}",
        )

    return ToolResult(
        success=False,
        error=output or f"flocks skills {subcommand} failed (exit {exit_code})",
        title=f"flocks skills {subcommand}",
    )
