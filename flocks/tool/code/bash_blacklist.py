"""Runtime blacklist checks for bash command execution."""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any, Iterable, Optional


DEFAULT_BLOCK_MESSAGE = "没有执行，黑名单命令已被拒绝：{command}"

_COMMAND_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "{", "}"}
_CONTROL_KEYWORDS = {
    "case",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "fi",
    "for",
    "function",
    "if",
    "in",
    "then",
    "until",
    "while",
}
_COMMAND_WRAPPERS = {"builtin", "command", "doas", "env", "exec", "nice", "nohup", "run0", "sudo", "time"}
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


@dataclass(frozen=True)
class BashBlacklistMatch:
    """A blocked bash command match."""

    command: str
    rule: str
    message: str


async def check_bash_blacklist(command: str) -> Optional[BashBlacklistMatch]:
    """Return blacklist match information when *command* is blocked."""
    from flocks.config.config import Config

    cfg = await Config.get()
    bash_cfg = getattr(cfg, "bash", None)
    command_black_list = _read_config_value(
        bash_cfg,
        "command_black_list",
        "commandBlacklist",
        "command_blacklist",
    )
    rules = _normalize_rules(command_black_list)
    if not rules:
        return None

    block_message = _read_config_value(bash_cfg, "block_message", "blockMessage") or DEFAULT_BLOCK_MESSAGE
    match = find_blacklisted_command(command, rules)
    if not match:
        return None

    blocked_command, rule = match
    return BashBlacklistMatch(
        command=blocked_command,
        rule=rule,
        message=str(block_message).replace("{command}", blocked_command),
    )


def find_blacklisted_command(command: str, rules: Iterable[str]) -> Optional[tuple[str, str]]:
    """Find the first command name in *command* that matches *rules*."""
    normalized_rules = _normalize_rules(rules)
    if not normalized_rules:
        return None

    for command_text in _iter_command_texts(command):
        for command_name in _extract_command_names(command_text):
            normalized = _normalize_command_name(command_name)
            if not normalized:
                continue
            for rule in normalized_rules:
                if _rule_matches(normalized, rule):
                    return normalized, rule
    return None


def _read_config_value(config: Any, *keys: str) -> Any:
    if config is None:
        return None
    if isinstance(config, dict):
        for key in keys:
            if key in config:
                return config[key]
        return None
    for key in keys:
        value = getattr(config, key, None)
        if value is not None:
            return value
    extra = getattr(config, "__pydantic_extra__", None) or {}
    if isinstance(extra, dict):
        for key in keys:
            if key in extra:
                return extra[key]
    return None


def _normalize_rules(rules: Any) -> list[str]:
    if not isinstance(rules, (list, tuple, set)):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        normalized_rule = _normalize_command_name(str(rule))
        if normalized_rule and normalized_rule not in seen:
            normalized.append(normalized_rule)
            seen.add(normalized_rule)
    return normalized


def _rule_matches(command_name: str, rule: str) -> bool:
    if any(ch in rule for ch in "*?[]"):
        return fnmatch.fnmatch(command_name, rule)
    return command_name == rule


def _normalize_command_name(command_name: str) -> str:
    name = command_name.strip().strip("\"'")
    if not name:
        return ""
    name = name.replace("\\", "/").rstrip("/")
    name = os.path.basename(name)
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def _iter_command_texts(command: str) -> Iterable[str]:
    yield command
    yield from _extract_command_substitutions(command)


def _extract_command_substitutions(command: str) -> Iterable[str]:
    """Yield simple command substitution bodies so `echo $(rm x)` is checked."""
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


def _extract_command_names(command: str) -> list[str]:
    tokens = _tokenize(command)
    names: list[str] = []
    expect_command = True
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if _is_separator(token):
            expect_command = True
            index += 1
            continue
        if not expect_command:
            index += 1
            continue
        if _should_skip_command_position_token(token):
            index += 1
            continue

        names.append(token)
        wrapper_name = _normalize_command_name(token)
        if wrapper_name in _COMMAND_WRAPPERS:
            wrapped = _find_wrapped_command(tokens[index + 1 :])
            if wrapped:
                names.append(wrapped)
        expect_command = False
        index += 1

    return names


def _tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return shlex.split(command, posix=False)


def _is_separator(token: str) -> bool:
    return token in _COMMAND_SEPARATORS or all(char in ";&|(){}" for char in token)


def _should_skip_command_position_token(token: str) -> bool:
    normalized = _normalize_command_name(token)
    return (
        not token
        or token == "!"
        or normalized in _CONTROL_KEYWORDS
        or _ASSIGNMENT_RE.match(token) is not None
        or token.startswith(("<", ">"))
    )


def _find_wrapped_command(tokens: list[str]) -> Optional[str]:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _is_separator(token):
            return None
        if token == "--":
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        if _ASSIGNMENT_RE.match(token):
            index += 1
            continue
        return token
    return None
