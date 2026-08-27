#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()

SOURCE_LOWER = "".join(["f", "l", "o", "c", "k", "s"])
SOURCE_TITLE = SOURCE_LOWER.capitalize()
SOURCE_UPPER = SOURCE_LOWER.upper()
SOURCE_STATE_DIR = f".{SOURCE_LOWER}"
SOURCE_HUB_LOWER = f"{SOURCE_LOWER}hub"
SOURCE_HUB_UPPER = SOURCE_HUB_LOWER.upper()

TARGET_LOWER = "smartclaw"
TARGET_BRAND = "SmartClaw"
TARGET_TITLE = "SmartClaw"
TARGET_UPPER = TARGET_LOWER.upper()
TARGET_STATE_DIR = f".{TARGET_LOWER}"
TARGET_HUB_LOWER = f"{TARGET_LOWER}hub"
TARGET_HUB_UPPER = TARGET_HUB_LOWER.upper()

REX_LOWER = "rex"
REX_TITLE = "Rex"
REX_UPPER = REX_LOWER.upper()
REX_INITIAL = REX_TITLE[0]
REX_INITIAL_LOWER = REX_LOWER[0]
REX_JUNIOR_LOWER = f"{REX_LOWER}_junior"
REX_JUNIOR_UPPER = REX_JUNIOR_LOWER.upper()
REX_JUNIOR_TITLE = "Rex_Junior"
REX_JUNIOR_HYPHEN_LOWER = f"{REX_LOWER}-junior"
REX_JUNIOR_HYPHEN_UPPER = REX_JUNIOR_HYPHEN_LOWER.upper()
REX_JUNIOR_HYPHEN_TITLE = "Rex-Junior"
REX_JUNIOR_CAMEL = "RexJunior"
REX_JUNIOR_LOWER_CAMEL = "rexJunior"

# SENTRY_LOWER = "sentry"
# SENTRY_TITLE = "Sentry"
# SENTRY_UPPER = SENTRY_LOWER.upper()
# SENTRY_INITIAL = SENTRY_TITLE[0]
# SENTRY_INITIAL_LOWER = SENTRY_LOWER[0]
# SENTRY_JUNIOR_LOWER = f"{SENTRY_LOWER}_junior"
# SENTRY_JUNIOR_UPPER = SENTRY_JUNIOR_LOWER.upper()
# SENTRY_JUNIOR_TITLE = "Sentry_Junior"
# SENTRY_JUNIOR_HYPHEN_LOWER = f"{SENTRY_LOWER}-junior"
# SENTRY_JUNIOR_HYPHEN_UPPER = SENTRY_JUNIOR_HYPHEN_LOWER.upper()
# SENTRY_JUNIOR_HYPHEN_TITLE = "Sentry-Junior"
# SENTRY_JUNIOR_CAMEL = "SentryJunior"
# SENTRY_JUNIOR_LOWER_CAMEL = "sentryJunior"

# 修改助手名字
SENTRY_LOWER = "titan"
SENTRY_TITLE = "Titan"
SENTRY_UPPER = SENTRY_LOWER.upper()
SENTRY_INITIAL = SENTRY_TITLE[0]
SENTRY_INITIAL_LOWER = SENTRY_LOWER[0]
SENTRY_JUNIOR_LOWER = f"{SENTRY_LOWER}_junior"
SENTRY_JUNIOR_UPPER = SENTRY_JUNIOR_LOWER.upper()
SENTRY_JUNIOR_TITLE = "Titan_Junior"
SENTRY_JUNIOR_HYPHEN_LOWER = f"{SENTRY_LOWER}-junior"
SENTRY_JUNIOR_HYPHEN_UPPER = SENTRY_JUNIOR_HYPHEN_LOWER.upper()
SENTRY_JUNIOR_HYPHEN_TITLE = "Titan-Junior"
SENTRY_JUNIOR_CAMEL = "TitanJunior"
SENTRY_JUNIOR_LOWER_CAMEL = "titanJunior"

# 【1. 目录名全局黑名单】
# 无论在哪个层级，只要文件夹名字匹配以下任意一项，直接跳过其内部所有文件
# 只能写“单独的文件夹名字”（不能带斜杠路径）
SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "automated_script",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
}

# 【2. 精准相对路径黑名单】
# 针对相对于项目根目录的具体文件或特定目录进行跳过（支持精准到单文件，不误伤其他同名文件）
SKIP_PATHS = {
    "aaa/bbb.py",
    ".smartclaw/plugins/tools/python/report_generator.py",
    ".smartclaw/plugins/tools/python/report_generator_scripts",

}

# 【3. 文件名全局黑名单】
# 无论在哪个目录，只要文件名匹配就跳过；默认包含脚本自身，防止脚本修改自身代码
SKIP_FILE_NAMES = {
    SCRIPT_PATH.name,  # 当前脚本自身 rebrand_smartclaw.py
}

# 【4. 特殊文本文件名白名单】
# 针对没有标准扩展名（如无后缀或以点开头）但属于纯文本的文件，允许进行内容替换
TEXT_FILE_NAMES = {
    "Dockerfile",
    "Makefile",
    ".dockerignore",
    ".gitignore",
    ".gitattributes",
    ".npmignore",
    ".env.example",
    ".env.local.example",
}

# 【5. 文本文件后缀白名单】
# 仅对以下扩展名的文本文件做内容替换，其余二进制文件（图片、音视频、压缩包等）自动跳过防损坏
TEXT_SUFFIXES = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".txt",
    ".css",
    ".scss",
    ".html",
    ".svg",
    ".ps1",
    ".sh",
    ".iss",
    ".service",
    ".sql",
    ".gitkeep",
    ".editorconfig",
    ".lock",
    ".sample",
    ".example",
}

# 【6. 正则内容保护规则】
# 匹配到的文本片段会先被临时占位符保护，替换完成后原样还原（用于防止外链 URL、哈希值等被误替换）
PROTECT_PATTERNS = [
    # re.compile(r"https?://[^\s'\"<>`]+"),   # 保护 HTTP/HTTPS 完整链接
    # re.compile(r"ghcr\.io/[^\s'\"<>`]+"),    # 保护容器镜像源地址
    # re.compile(r"ghcr\.nju\.edu\.cn/[^\s'\"<>`]+"),
    # re.compile(r"\bsha(?:1|224|256|384|512)-[A-Za-z0-9+/=._-]+"),  # 保护 sha 哈希校验码
    # re.compile(r"\bsha(?:1|224|256|384|512):[A-Fa-f0-9]+\b"),
    # re.compile(r"(?i)\bt-rex\b"),   # 保护特定专属词汇
]

CONTENT_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\.{re.escape(SOURCE_LOWER)}\b"), TARGET_STATE_DIR),
    (re.compile(rf"\b{re.escape(SOURCE_HUB_UPPER)}\b"), TARGET_HUB_UPPER),
    (re.compile(rf"\b{re.escape(SOURCE_HUB_LOWER)}\b"), TARGET_HUB_LOWER),
    (re.compile(rf"\b{re.escape(SOURCE_UPPER)}\b"), TARGET_UPPER),
    (re.compile(re.escape(SOURCE_UPPER)), TARGET_UPPER),
    (re.compile(rf"\b{re.escape(SOURCE_TITLE)}\b"), TARGET_BRAND),
    (re.compile(re.escape(SOURCE_TITLE)), TARGET_TITLE),
    (re.compile(rf"\b{re.escape(SOURCE_LOWER)}\b"), TARGET_LOWER),
    (re.compile(re.escape(SOURCE_LOWER)), TARGET_LOWER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_UPPER)}\b"), SENTRY_JUNIOR_UPPER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_TITLE)}\b"), SENTRY_JUNIOR_TITLE),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_HYPHEN_UPPER)}\b"), SENTRY_JUNIOR_HYPHEN_UPPER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_HYPHEN_TITLE)}\b"), SENTRY_JUNIOR_HYPHEN_TITLE),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_LOWER)}\b"), SENTRY_JUNIOR_LOWER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_HYPHEN_LOWER)}\b"), SENTRY_JUNIOR_HYPHEN_LOWER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_CAMEL)}\b"), SENTRY_JUNIOR_CAMEL),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_LOWER_CAMEL)}\b"), SENTRY_JUNIOR_LOWER_CAMEL),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_UPPER)}(?=-)"), SENTRY_UPPER),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_TITLE)}(?=-)"), SENTRY_TITLE),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_LOWER)}(?=-)"), SENTRY_LOWER),
    (re.compile(rf"(?<=-){re.escape(REX_UPPER)}(?=-)"), SENTRY_UPPER),
    (re.compile(rf"(?<=-){re.escape(REX_TITLE)}(?=-)"), SENTRY_TITLE),
    (re.compile(rf"(?<=-){re.escape(REX_LOWER)}(?=-)"), SENTRY_LOWER),
    (re.compile(rf"(?<=-){re.escape(REX_UPPER)}(?![A-Za-z0-9-])"), SENTRY_UPPER),
    (re.compile(rf"(?<=-){re.escape(REX_TITLE)}(?![A-Za-z0-9-])"), SENTRY_TITLE),
    (re.compile(rf"(?<=-){re.escape(REX_LOWER)}(?![A-Za-z0-9-])"), SENTRY_LOWER),
    (re.compile(rf"{re.escape(REX_UPPER)}(?=[A-Z])"), SENTRY_UPPER),
    (re.compile(rf"{re.escape(REX_TITLE)}(?=[A-Z])"), SENTRY_TITLE),
    (re.compile(rf"{re.escape(REX_LOWER)}(?=[A-Z])"), SENTRY_LOWER),
    (re.compile(rf"(?<=[A-Za-z0-9]){re.escape(REX_TITLE)}(?![A-Za-z0-9-])"), SENTRY_TITLE),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_UPPER)}(?![A-Za-z0-9-])"), SENTRY_UPPER),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_TITLE)}(?![A-Za-z0-9-])"), SENTRY_TITLE),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_LOWER)}(?![A-Za-z0-9-])"), SENTRY_LOWER),
    (re.compile(rf"(?<=>){re.escape(REX_INITIAL)}(?=</span>\s*{re.escape(SENTRY_TITLE)}(?:\b|_|-))"), SENTRY_INITIAL),
    (re.compile(rf"(?<=>){re.escape(REX_INITIAL)}(?=</span>\s*{re.escape(SENTRY_UPPER)}(?:\b|_|-))"), SENTRY_INITIAL),
    (re.compile(rf"(?<=>){re.escape(REX_INITIAL_LOWER)}(?=</span>\s*{re.escape(SENTRY_LOWER)}(?:\b|_|-))"), SENTRY_INITIAL_LOWER),
]

PATH_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(re.escape(SOURCE_STATE_DIR)), TARGET_STATE_DIR),
    (re.compile(re.escape(SOURCE_HUB_UPPER)), TARGET_HUB_UPPER),
    (re.compile(re.escape(SOURCE_HUB_LOWER)), TARGET_HUB_LOWER),
    (re.compile(re.escape(SOURCE_TITLE)), TARGET_TITLE),
    (re.compile(re.escape(SOURCE_UPPER)), TARGET_UPPER),
    (re.compile(re.escape(SOURCE_LOWER)), TARGET_LOWER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_UPPER)}\b"), SENTRY_JUNIOR_UPPER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_TITLE)}\b"), SENTRY_JUNIOR_TITLE),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_HYPHEN_UPPER)}\b"), SENTRY_JUNIOR_HYPHEN_UPPER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_HYPHEN_TITLE)}\b"), SENTRY_JUNIOR_HYPHEN_TITLE),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_LOWER)}\b"), SENTRY_JUNIOR_LOWER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_HYPHEN_LOWER)}\b"), SENTRY_JUNIOR_HYPHEN_LOWER),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_CAMEL)}\b"), SENTRY_JUNIOR_CAMEL),
    (re.compile(rf"\b{re.escape(REX_JUNIOR_LOWER_CAMEL)}\b"), SENTRY_JUNIOR_LOWER_CAMEL),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_UPPER)}(?=-)"), SENTRY_UPPER),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_TITLE)}(?=-)"), SENTRY_TITLE),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_LOWER)}(?=-)"), SENTRY_LOWER),
    (re.compile(rf"(?<=-){re.escape(REX_UPPER)}(?=-)"), SENTRY_UPPER),
    (re.compile(rf"(?<=-){re.escape(REX_TITLE)}(?=-)"), SENTRY_TITLE),
    (re.compile(rf"(?<=-){re.escape(REX_LOWER)}(?=-)"), SENTRY_LOWER),
    (re.compile(rf"(?<=-){re.escape(REX_UPPER)}(?![A-Za-z0-9-])"), SENTRY_UPPER),
    (re.compile(rf"(?<=-){re.escape(REX_TITLE)}(?![A-Za-z0-9-])"), SENTRY_TITLE),
    (re.compile(rf"(?<=-){re.escape(REX_LOWER)}(?![A-Za-z0-9-])"), SENTRY_LOWER),
    (re.compile(rf"{re.escape(REX_UPPER)}(?=[A-Z])"), SENTRY_UPPER),
    (re.compile(rf"{re.escape(REX_TITLE)}(?=[A-Z])"), SENTRY_TITLE),
    (re.compile(rf"{re.escape(REX_LOWER)}(?=[A-Z])"), SENTRY_LOWER),
    (re.compile(rf"(?<=[A-Za-z0-9]){re.escape(REX_TITLE)}(?![A-Za-z0-9-])"), SENTRY_TITLE),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_UPPER)}(?![A-Za-z0-9-])"), SENTRY_UPPER),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_TITLE)}(?![A-Za-z0-9-])"), SENTRY_TITLE),
    (re.compile(rf"(?<![A-Za-z0-9-]){re.escape(REX_LOWER)}(?![A-Za-z0-9-])"), SENTRY_LOWER),
]

TARGET_FAVICON_GLYPH = next(char.upper() for char in TARGET_BRAND if char.isalnum())

TARGET_FAVICON_SVG = f"""<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\">
  <defs>
    <linearGradient id=\"bg\" x1=\"12\" y1=\"8\" x2=\"52\" y2=\"56\" gradientUnits=\"userSpaceOnUse\">
      <stop offset=\"0\" stop-color=\"#1e293b\" />
      <stop offset=\"1\" stop-color=\"#0f172a\" />
    </linearGradient>
    <linearGradient id=\"accent\" x1=\"18\" y1=\"12\" x2=\"46\" y2=\"52\" gradientUnits=\"userSpaceOnUse\">
      <stop offset=\"0\" stop-color=\"#ef4444\" />
      <stop offset=\"1\" stop-color=\"#b91c1c\" />
    </linearGradient>
  </defs>
  <rect x=\"6\" y=\"6\" width=\"52\" height=\"52\" rx=\"14\" fill=\"url(#bg)\" />
  <text
    x=\"31.5\"
    y=\"41\"
    text-anchor=\"middle\"
    font-size=\"36\"
    font-family=\"Inter, 'Segoe UI', Arial, sans-serif\"
    font-weight=\"800\"
    font-style=\"italic\"
    fill=\"#f8fafc\"
  >{TARGET_FAVICON_GLYPH}</text>
  <path
    d=\"M40 16l1.8 4.4L46 22.2l-4.2 1.7L40 28l-1.8-4.1L34 22.2l4.2-1.8L40 16Z\"
    fill=\"url(#accent)\"
  />
</svg>
"""

SPECIAL_FILE_UPDATES = {
    "webui/public/favicon.svg": TARGET_FAVICON_SVG,
}


@dataclass
class LoadedText:
    text: str
    encoding: str


@dataclass
class ChangeSummary:
    modified_files: set[str]
    renamed_paths: list[tuple[str, str]]
    preserved_urls: set[str]
    skipped_binary_files: set[str]


def is_text_file(path: Path) -> bool:
    if path.name in TEXT_FILE_NAMES:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


# def should_skip(path: Path) -> bool:
#     if path.name in SKIP_FILE_NAMES:
#         return True
#     return any(part in SKIP_DIR_NAMES for part in path.parts)


def should_skip(path: Path) -> bool:
    if path.name in SKIP_FILE_NAMES:
        return True
    if any(part in SKIP_DIR_NAMES for part in path.parts):
        return True

    # ====== 新增以下 4 行逻辑 ======
    try:
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == p or rel.startswith(f"{p}/") for p in SKIP_PATHS):
            return True
    except ValueError:
        pass
    # ==============================

    return False


def iter_repo_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if should_skip(path):
            continue
        if path.is_file():
            yield path


def load_text(path: Path) -> LoadedText | None:
    raw = path.read_bytes()
    if b"\x00" in raw:
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return LoadedText(raw.decode("utf-8-sig"), "utf-8-sig")
        except UnicodeDecodeError:
            return None
    try:
        return LoadedText(raw.decode("utf-8"), "utf-8")
    except UnicodeDecodeError:
        return None


def write_text(path: Path, content: str, encoding: str) -> None:
    with path.open("w", encoding=encoding, newline="") as handle:
        handle.write(content)


def protect_fragments(text: str, preserved_urls: set[str]) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    counter = 0

    def protect_match(match: re.Match[str]) -> str:
        nonlocal counter
        value = match.group(0)
        key = f"__SMARTCLAW_KEEP_{counter}__"
        counter += 1
        placeholders[key] = value
        preserved_urls.add(value)
        return key

    protected = text
    for pattern in PROTECT_PATTERNS:
        protected = pattern.sub(protect_match, protected)
    return protected, placeholders


def restore_fragments(text: str, placeholders: dict[str, str]) -> str:
    restored = text
    for key, value in placeholders.items():
        restored = restored.replace(key, value)
    return restored


def transform_content(text: str, preserved_urls: set[str]) -> str:
    protected, placeholders = protect_fragments(text, preserved_urls)
    result = protected
    for pattern, replacement in CONTENT_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    return restore_fragments(result, placeholders)


def transform_name(name: str) -> str:
    protected, placeholders = protect_fragments(name, set())
    result = protected
    for pattern, replacement in PATH_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    return restore_fragments(result, placeholders)


def update_file_contents(root: Path, apply: bool, summary: ChangeSummary) -> None:
    print("正在扫描并处理文本文件内容……")
    for path in iter_repo_files(root):
        rel = path.relative_to(root).as_posix()
        if not is_text_file(path):
            summary.skipped_binary_files.add(rel)
            continue
        loaded = load_text(path)
        if loaded is None:
            summary.skipped_binary_files.add(rel)
            continue
        updated = transform_content(loaded.text, summary.preserved_urls)
        if updated == loaded.text:
            continue
        summary.modified_files.add(rel)
        print(f"[内容更新] {rel}")
        if apply:
            write_text(path, updated, loaded.encoding)


def update_special_files(root: Path, apply: bool, summary: ChangeSummary) -> None:
    print("正在处理需要单独覆盖的特殊文件……")
    for rel, expected in SPECIAL_FILE_UPDATES.items():
        path = root / rel
        if not path.is_file():
            continue
        loaded = load_text(path)
        if loaded is None:
            summary.skipped_binary_files.add(rel)
            continue
        if loaded.text == expected:
            continue
        summary.modified_files.add(rel)
        print(f"[特殊文件更新] {rel}")
        if apply:
            write_text(path, expected, loaded.encoding)


def rename_paths(root: Path, apply: bool, summary: ChangeSummary) -> None:
    print("正在扫描并处理路径重命名……")
    paths = [path for path in root.rglob("*") if not should_skip(path)]
    paths.sort(key=lambda item: (len(item.parts), str(item)), reverse=True)

    for path in paths:
        if path == root:
            continue
        new_name = transform_name(path.name)
        if new_name == path.name:
            continue
        target = path.with_name(new_name)
        before = path.relative_to(root).as_posix()
        after = target.relative_to(root).as_posix()
        summary.renamed_paths.append((before, after))
        print(f"[路径重命名] {before} -> {after}")
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            f"将仓库品牌从 {SOURCE_LOWER} 替换为 {TARGET_BRAND}，"
            f"并将 {REX_LOWER} 替换为 {SENTRY_TITLE}。"
        )
    )
    parser.add_argument("--apply", action="store_true", help="直接将品牌替换结果写入文件。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = ChangeSummary(
        modified_files=set(),
        renamed_paths=[],
        preserved_urls=set(),
        skipped_binary_files=set(),
    )

    mode_text = "正式执行模式" if args.apply else "预演模式"
    print(f"开始执行品牌替换脚本（{mode_text}）。")
    print(
        f"替换目标：{SOURCE_LOWER} -> {TARGET_BRAND}，{REX_LOWER} -> {SENTRY_TITLE}。"
    )

    update_file_contents(ROOT, args.apply, summary)
    update_special_files(ROOT, args.apply, summary)
    rename_paths(ROOT, args.apply, summary)

    print("处理完成，结果如下：")
    print(f"- 修改文件数：{len(summary.modified_files)}")
    print(f"- 重命名路径数：{len(summary.renamed_paths)}")
    print(f"- 受保护片段数：{len(summary.preserved_urls)}")
    print(f"- 跳过的二进制文件数：{len(summary.skipped_binary_files)}")
    if args.apply:
        print("本次为正式执行，变更已写入文件。")
    else:
        print("本次为预演模式，未写入任何文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
