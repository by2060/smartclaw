"""Risk classification for shell command execution."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Iterable, Optional

_COMMAND_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "{", "}"}
_COMMAND_WRAPPERS = {"builtin", "command", "doas", "env", "exec", "nice", "nohup", "run0", "sudo", "time"}
_SHELL_INTERPRETERS = {"bash", "dash", "fish", "ksh", "ksh93", "mksh", "sh", "zsh"}
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_STOP_SCRIPT_NAMES = {"stop.sh", "restart.sh"}
_PROCESS_CONTROL = {"kill", "killall", "pkill"}
_FIREWALL_TOOLS = {"firewall-cmd", "iptables", "ip6tables", "nft", "ufw"}


@dataclass(frozen=True)
class ShellRisk:
    """Structured shell risk finding."""

    level: str
    risk_type: str
    command: str
    reason: str
    matched_command: str
    targets: list[str] = field(default_factory=list)

    def metadata(self) -> dict:
        return {
            "risk_level": self.level,
            "risk_type": self.risk_type,
            "reason": self.reason,
            "matched_command": self.matched_command,
            "targets": self.targets,
            "blocked_by_policy": True,
        }


def classify_shell_risk(command: str) -> Optional[ShellRisk]:
    """Return a high-risk finding for operationally dangerous shell commands."""
    for tokens in _iter_command_tokens(command):
        risk = _classify_tokens(command, tokens)
        if risk:
            return risk
    return None


def high_risk_block_message(_risk: ShellRisk) -> str:
    return (
        "这个操作可能会停止服务、终止进程或影响网络访问，出于安全考虑，我不能直接执行。"
        "建议先确认影响范围，并由具备运维权限的人员在受控环境中手动处理。"
    )


def _classify_tokens(original_command: str, tokens: list[str]) -> Optional[ShellRisk]:
    command_index = _find_command_index(tokens)
    if command_index is None:
        return None

    command_token = tokens[command_index]
    command_name = _normalize_command_name(command_token)
    args = tokens[command_index + 1 :]

    if command_name in _COMMAND_WRAPPERS:
        wrapped_index = _find_command_index(args)
        if wrapped_index is not None:
            return _classify_tokens(original_command, args[wrapped_index:])

    if command_name in _SHELL_INTERPRETERS:
        script_risk = _classify_script_path(original_command, command_token, args)
        if script_risk:
            return script_risk

    script_risk = _classify_script_path(original_command, command_token, args)
    if script_risk:
        return script_risk

    if command_name == "systemctl" and any(arg in {"stop", "restart"} for arg in args):
        action = next(arg for arg in args if arg in {"stop", "restart"})
        return _risk(original_command, f"service_{action}", command_token, "systemctl service control", args)

    if command_name == "service" and any(arg in {"stop", "restart"} for arg in args):
        action = next(arg for arg in args if arg in {"stop", "restart"})
        return _risk(original_command, f"service_{action}", command_token, "service control", args)

    if command_name in _PROCESS_CONTROL:
        return _risk(original_command, "process_control", command_token, "process termination", args)

    if command_name == "docker" and any(arg in {"kill", "restart", "stop"} for arg in args):
        action = next(arg for arg in args if arg in {"kill", "restart", "stop"})
        return _risk(original_command, f"container_{action}", command_token, "container lifecycle control", args)

    if command_name in {"docker-compose", "podman"} and any(arg in {"kill", "restart", "stop"} for arg in args):
        action = next(arg for arg in args if arg in {"kill", "restart", "stop"})
        return _risk(original_command, f"container_{action}", command_token, "container lifecycle control", args)

    if command_name == "kubectl" and _is_high_risk_kubectl(args):
        return _risk(original_command, "kubernetes_control", command_token, "kubernetes workload control", args)

    if command_name in _FIREWALL_TOOLS:
        return _risk(original_command, "firewall_change", command_token, "firewall or packet filter change", args)

    return None


def _classify_script_path(original_command: str, command_token: str, args: list[str]) -> Optional[ShellRisk]:
    candidates = [command_token, *args]
    for candidate in candidates:
        normalized = _normalize_path_name(candidate)
        if normalized in _STOP_SCRIPT_NAMES:
            risk_type = "service_stop" if normalized == "stop.sh" else "service_restart"
            return _risk(original_command, risk_type, candidate, f"service control script {normalized}", args)
    return None


def _is_high_risk_kubectl(args: list[str]) -> bool:
    if not args:
        return False
    if "delete" in args:
        return True
    for index, arg in enumerate(args[:-1]):
        if arg == "rollout" and args[index + 1] in {"restart", "undo"}:
            return True
    return False


def _risk(
    original_command: str,
    risk_type: str,
    matched_command: str,
    reason: str,
    args: Iterable[str],
) -> ShellRisk:
    return ShellRisk(
        level="high",
        risk_type=risk_type,
        command=original_command,
        reason=reason,
        matched_command=matched_command,
        targets=[arg for arg in args if not arg.startswith("-")][:5],
    )


def _iter_command_tokens(command: str) -> Iterable[list[str]]:
    for command_text in _iter_command_texts(command):
        tokens = _tokenize(command_text)
        current: list[str] = []
        for token in tokens:
            if _is_separator(token):
                if current:
                    yield current
                    current = []
                continue
            current.append(token)
        if current:
            yield current


def _iter_command_texts(command: str) -> Iterable[str]:
    yield command
    index = 0
    while index < len(command):
        start = command.find("$(", index)
        if start < 0:
            break
        depth = 1
        pos = start + 2
        while pos < len(command) and depth:
            char = command[pos]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            pos += 1
        if depth == 0:
            body = command[start + 2 : pos - 1]
            if body.strip():
                yield body
        index = max(pos, start + 2)

    for match in re.finditer(r"`([^`]+)`", command):
        body = match.group(1)
        if body.strip():
            yield body


def _tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return shlex.split(command, posix=False)


def _find_command_index(tokens: list[str]) -> Optional[int]:
    for index, token in enumerate(tokens):
        if not token or token == "!" or token.startswith(("<", ">")):
            continue
        if _ASSIGNMENT_RE.match(token):
            continue
        return index
    return None


def _is_separator(token: str) -> bool:
    return token in _COMMAND_SEPARATORS or all(char in ";&|(){}" for char in token)


def _normalize_command_name(command_name: str) -> str:
    name = _normalize_path_name(command_name)
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def _normalize_path_name(path: str) -> str:
    name = path.strip().strip("\"'")
    if not name:
        return ""
    name = name.replace("\\", "/").rstrip("/")
    return os.path.basename(name).lower()
